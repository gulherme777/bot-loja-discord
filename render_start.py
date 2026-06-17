import discord
from discord import app_commands
import requests
from flask import Flask, request
import threading
import asyncio
import os
import sys
import time
import base64
import json
from datetime import datetime
from io import BytesIO
import pyotp

print("🔧 Iniciando bot da G7 STORE...")

# ===============================
# CONFIGURAÇÕES
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
INFINITE_TAG = "guilherme_vinicius90"

# Se estiver testando localmente com .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", DISCORD_TOKEN)
except ImportError:
    pass

# Verificar se o token existe
if not DISCORD_TOKEN:
    print("❌ ERRO CRÍTICO: DISCORD_TOKEN não encontrado nas variáveis de ambiente!")
    print("Por favor, adicione DISCORD_TOKEN no painel do Render.")
    # Não vamos dar sys.exit para não entrar em loop infinito de crash no Render
    # Mas o bot não vai rodar sem o token.

WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL",
    f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
)

# Arquivos de dados
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

# IDs do Discord
GUILD_ID = 1472114509068898367
CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
MEU_ID = 1431125477069688953

carrinhos_ativos = {}

# LOCKS PARA THREAD SAFETY
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()

# ===============================
# SISTEMA DE PERSISTÊNCIA
# ===============================
def carregar_json(caminho, default=None):
    if os.path.exists(caminho):
        try:
            with open(caminho, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Erro ao carregar {caminho}: {e}")
    return default if default is not None else {}

def salvar_json(caminho, dados):
    try:
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Erro ao salvar {caminho}: {e}")

def salvar_estoque(estoque): salvar_json(ARQUIVO_ESTOQUE_JSON, estoque)
def salvar_produtos(produtos): salvar_json(ARQUIVO_PRODUTOS_JSON, produtos)
def salvar_pagamentos_processados(pagamentos): salvar_json(ARQUIVO_PAGAMENTOS_PROCESSADOS, list(pagamentos))

pagamentos_processados = set(carregar_json(ARQUIVO_PAGAMENTOS_PROCESSADOS, []))
estoque_disponivel = carregar_json(ARQUIVO_ESTOQUE_JSON)
produtos_disponiveis = carregar_json(ARQUIVO_PRODUTOS_JSON)

# ===============================
# FUNÇÕES AUXILIARES
# ===============================
def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    try:
        valor_float = float(preco)
        if valor_float < 1.0:
            return {"erro": "A InfinitePay exige valor mínimo de R$ 1,00."}
            
        preco_centavos = int(round(valor_float * 100))
        payload = {
            "handle": INFINITE_TAG,
            "order_nsu": f"{produto_id}_{user_id}_{int(time.time())}",
            "items": [{"quantity": 1, "price": preco_centavos, "description": f"Compra: {nome_produto}"[:60]}]
        }
        if WEBHOOK_URL and WEBHOOK_URL.startswith("https"):
            payload["webhook_url"] = WEBHOOK_URL

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.post("https://api.checkout.infinitepay.io/links", json=payload, headers=headers, timeout=15)
        
        if response.status_code in [200, 201]:
            data = response.json()
            return {"payment_url": data.get("url"), "produto": nome_produto, "preco": float(preco), "payment_id": data.get("invoice_slug"), "produto_id": produto_id}
        return {"erro": f"InfinitePay {response.status_code}: {response.text}"}
    except Exception as e:
        return {"erro": str(e)}

def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return None
        if variacao_nome:
            itens = estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, [])
            if itens:
                item = itens.pop(0)
                salvar_estoque(estoque_disponivel)
                return item
            return None
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens:
            item = itens.pop(0)
            salvar_estoque(estoque_disponivel)
            return item
        return None

def verificar_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return 0
        if variacao_nome: return len(estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, []))
        produto_info = produtos_disponiveis.get(produto_id, {})
        variacoes = produto_info.get("variacoes", [])
        if variacoes:
            return sum(len(estoque_disponivel[produto_id].get("variacoes", {}).get(v["nome"], [])) for v in variacoes)
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# LOGS DISCORD
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal: 
            print(f"⚠️ Canal de carrinhos ({CANAL_CARRINHOS}) não encontrado.")
            return None
        embed = discord.Embed(title="🛒 NOVO CARRINHO ATIVO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {"canal": canal.id, "mensagem_id": mensagem.id, "usuario": user.id, "produto": produto_nome}
        return mensagem
    except Exception as e:
        print(f"❌ Erro log_carrinho_ativo: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(title="✅ PAGAMENTO CONFIRMADO", color=0x00ff88, timestamp=datetime.now())
            embed.add_field(name="Cliente", value=user.mention, inline=True)
            embed.add_field(name="Produto", value=produto_nome, inline=True)
            embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
            if item_entregue:
                embed.add_field(name="🔐 Item Entregue", value=f"```{item_entregue}```", inline=False)
            await canal_pagos.send(embed=embed)
        else:
            print(f"⚠️ Canal de pagos ({CANAL_PAGOS}) não encontrado.")
            
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    embed_aprovado = discord.Embed(title="✅ PAGAMENTO APROVADO", description=f"Cliente: {user.mention}\nProduto: {produto_nome}\nValor: R$ {valor:.2f}", color=0x00ff88)
                    await msg.edit(embed=embed_aprovado)
                except: pass
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e:
        print(f"❌ Erro log_pagamento_confirmado: {e}")

# ===============================
# VIEWS E MODAIS
# ===============================
class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
    chave = discord.ui.TextInput(label="Chave 2FA", placeholder="Cole sua chave aqui...", min_length=16, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            totp = pyotp.TOTP(self.chave.value.strip().upper())
            codigo = totp.now()
            embed = discord.Embed(title="🔐 CÓDIGO 2FA", description=f"```{codigo}```", color=0x00ff88)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

class VariacoesView(discord.ui.View):
    def __init__(self, produto_id, produto_nome, variacoes):
        super().__init__(timeout=300)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes
        options = [discord.SelectOption(label=v["nome"], description=f"R$ {v['preco']:.2f}", value=str(i)) for i, v in enumerate(variacoes)]
        select = discord.ui.Select(placeholder="Escolha uma opção...", options=options, custom_id="select_variacao")
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        indice = int(interaction.data["values"][0])
        v = self.variacoes[indice]
        pix_data = criar_pagamento_pix_com_preco(interaction.user.id, f"{self.produto_id}_{v['nome']}", v["preco"], f"{self.produto_nome} - {v['nome']}")
        if "erro" in pix_data:
            await interaction.followup.send(f"❌ Erro: {pix_data['erro']}", ephemeral=True)
            return
        await log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data['payment_id'])
        embed = discord.Embed(title="🧾 PAGAMENTO - G7 STORE", description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para pagar.", color=0x00ff88)
        view = discord.ui.View(); view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=pix_data['payment_url']))
        await interaction.user.send(embed=embed, view=view)
        await interaction.followup.send("📨 Link enviado no privado!", ephemeral=True)

async def criar_embed_produto_tzada(produto_id, p_info):
    qtd = verificar_estoque(produto_id)
    tipo = "🤖 Entrega Automática!" if p_info.get('tipo') == 'auto' else "👨‍💼 Entrega Manual"
    desc = p_info.get('descricao', '').replace('|', '\n✅ ')
    embed = discord.Embed(title=f"⚡ {tipo}", description=f"**{p_info['nome']}**\n\n✅ {desc}\n\n📦 Estoque: {qtd}", color=0xffa500)
    if p_info.get('imagem'): embed.set_image(url=p_info['imagem'])
    embed.add_field(name="💰 Valor", value=f"R$ {p_info['preco']:.2f}", inline=True)
    return embed

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id, produto_nome, variacoes=None):
        super().__init__(timeout=None)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes or []

    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_comprar")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if self.variacoes:
            await interaction.followup.send("Selecione a opção:", view=VariacoesView(self.produto_id, self.produto_nome, self.variacoes), ephemeral=True)
            return
        p_info = produtos_disponiveis[self.produto_id]
        pix_data = criar_pagamento_pix_com_preco(interaction.user.id, self.produto_id, p_info["preco"], self.produto_nome)
        if "erro" in pix_data:
            await interaction.followup.send(f"❌ Erro: {pix_data['erro']}", ephemeral=True)
            return
        await log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data['payment_id'])
        embed = discord.Embed(title="🧾 PAGAMENTO - G7 STORE", description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para pagar.", color=0x00ff88)
        view = discord.ui.View(); view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=pix_data['payment_url']))
        await interaction.user.send(embed=embed, view=view)
        await interaction.followup.send("📨 Link enviado no privado!", ephemeral=True)

# ===============================
# DISCORD BOT CLIENT
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("✅ Slash commands sincronizados")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")
        await self.change_presence(activity=discord.Game(name="G7 STORE 💎"))

bot = Bot()

# ===============================
# COMANDOS SLASH
# ===============================
@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao_indice: int = -1):
    if interaction.user.id != MEU_ID: return
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    with estoque_lock:
        if produto_id not in estoque_disponivel: estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
        if variacao_indice >= 0:
            v_nome = produtos_disponiveis[produto_id]["variacoes"][variacao_indice]["nome"]
            if v_nome not in estoque_disponivel[produto_id]["variacoes"]: estoque_disponivel[produto_id]["variacoes"][v_nome] = []
            estoque_disponivel[produto_id]["variacoes"][v_nome].extend(novos)
        else:
            estoque_disponivel[produto_id]["itens"].extend(novos)
        salvar_estoque(estoque_disponivel)
    await interaction.response.send_message(f"✅ {len(novos)} itens adicionados!", ephemeral=True)

@bot.tree.command(name="configurar_produto", description="[ADMIN] Enviar mensagem de compra")
async def configurar_produto(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    if interaction.user.id != MEU_ID: return
    p_info = produtos_disponiveis[produto_id]
    embed = await criar_embed_produto_tzada(produto_id, p_info)
    view = ProdutoCompraView(produto_id, p_info["nome"], p_info.get("variacoes", []))
    await canal.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Configurado!", ephemeral=True)

# ===============================
# KEEP ALIVE
# ===============================
def keep_alive_ping():
    url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/"
    while True:
        try:
            time.sleep(600)
            if "localhost" not in url:
                requests.get(url, timeout=10)
        except: pass

# ===============================
# WEBHOOK & FLASK
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return f"🤖 G7 STORE ONLINE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json if request.is_json else {}
    payment_id = data.get('invoice_slug')
    ref = data.get('order_nsu', '')
    if not payment_id: return "OK", 200
    with webhook_lock:
        if str(payment_id) in pagamentos_processados: return "OK", 200
        try:
            pagamentos_processados.add(str(payment_id))
            salvar_pagamentos_processados(pagamentos_processados)
            if ref:
                partes = ref.split('_')
                if len(partes) >= 3:
                    p_id, u_id = partes[0], int(partes[-2])
                    v_nome = partes[1] if len(partes) == 4 else None
                    user = bot.get_user(u_id)
                    if not user:
                        try:
                            future = asyncio.run_coroutine_threadsafe(bot.fetch_user(u_id), bot.loop)
                            user = future.result(timeout=10)
                        except: pass
                    if user and p_id in produtos_disponiveis:
                        p_info = produtos_disponiveis[p_id]
                        item = entregar_do_estoque(p_id, v_nome) if p_info.get("tipo") == "auto" else None
                        msg = f"✅ **Sua compra chegou!**\n\n📦 **{p_info['nome']}**\n\n🔐 **Produto:**\n```{item}```" if item else f"✅ Pagamento confirmado para **{p_info['nome']}**! Entrega manual em breve."
                        asyncio.run_coroutine_threadsafe(user.send(msg), bot.loop)
                        # Log no canal de pagos
                        asyncio.run_coroutine_threadsafe(log_pagamento_confirmado(user, p_info['nome'], data.get('amount', 0)/100, payment_id, item), bot.loop)
        except Exception as e: print(f"❌ Erro Webhook: {e}")
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    if DISCORD_TOKEN:
        threading.Thread(target=run_flask, daemon=True).start()
        threading.Thread(target=keep_alive_ping, daemon=True).start()
        print("🚀 Iniciando Bot...")
        while True:
            try:
                bot.run(DISCORD_TOKEN)
            except Exception as e:
                print(f"🧨 Erro de conexão: {e}. Reiniciando em 15s...")
                time.sleep(15)
    else:
        print("❌ Bot não pode ser iniciado sem DISCORD_TOKEN.")
        # Mantém o Flask vivo para o Render não dar erro de porta
        run_flask()
