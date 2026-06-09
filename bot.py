import discord
from discord import app_commands
import mercadopago
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
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")

# Se estiver testando localmente com .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", DISCORD_TOKEN)
    MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", MP_ACCESS_TOKEN)
except ImportError:
    pass

# Verificar se o token existe
if not DISCORD_TOKEN:
    print("❌ ERRO: DISCORD_TOKEN não encontrado!")
    print("Configure a variável de ambiente DISCORD_TOKEN no Render")
    # No Render, não queremos dar exit(1) imediatamente se estivermos em build, 
    # mas para execução é necessário.
    if os.environ.get("RENDER"):
        print("Aguardando configuração de ambiente...")

WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL",
    f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
)

# Arquivos de dados
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

# IDs do Discord (Configurados conforme seu código original)
GUILD_ID = 1472114509068898367
CARGO_MEMBRO = 1472666559049633952
CARGO_CLIENTE = 1472666841515032676
CANAL_CARRINHOS = 1473180070851117108
CANAL_PAGOS = 1473182832225554554
MEU_ID = 736643333840961547
CARGO_ADMIN = 1472666559049633952

carrinhos_ativos = {}

# ===============================
# LOCKS PARA THREAD SAFETY
# ===============================
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()

# ===============================
# SISTEMA DE PERSISTÊNCIA
# ===============================
def carregar_pagamentos_processados():
    if os.path.exists(ARQUIVO_PAGAMENTOS_PROCESSADOS):
        try:
            with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except: return set()
    return set()

def salvar_pagamentos_processados(pagamentos):
    with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'w', encoding='utf-8') as f:
        json.dump(list(pagamentos), f, indent=2)

def carregar_estoque():
    if os.path.exists(ARQUIVO_ESTOQUE_JSON):
        try:
            with open(ARQUIVO_ESTOQUE_JSON, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def salvar_estoque(estoque):
    with open(ARQUIVO_ESTOQUE_JSON, 'w', encoding='utf-8') as f:
        json.dump(estoque, f, indent=2, ensure_ascii=False)

def carregar_produtos():
    if os.path.exists(ARQUIVO_PRODUTOS_JSON):
        try:
            with open(ARQUIVO_PRODUTOS_JSON, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}
    return {}

def salvar_produtos(produtos):
    with open(ARQUIVO_PRODUTOS_JSON, 'w', encoding='utf-8') as f:
        json.dump(produtos, f, indent=2, ensure_ascii=False)

pagamentos_processados = carregar_pagamentos_processados()
estoque_disponivel = carregar_estoque()
produtos_disponiveis = carregar_produtos()

# ===============================
# MERCADO PAGO
# ===============================
sdk = None
if MP_ACCESS_TOKEN:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    print("✅ SDK Mercado Pago inicializado")
else:
    print("⚠️ MP_ACCESS_TOKEN não configurado. Pagamentos PIX falharão.")

def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    if not sdk:
        print("❌ Erro: SDK Mercado Pago não configurado.")
        return None
    try:
        preco_formatado = round(float(preco), 2)
        payment_data = {
            "transaction_amount": preco_formatado,
            "description": f"Compra: {nome_produto}"[:60],
            "payment_method_id": "pix",
            "payer": {
                "email": f"c_{user_id}@cliente.com",
                "first_name": "Cliente",
                "last_name": str(user_id)
            },
            "external_reference": f"{produto_id}_{user_id}_{int(time.time())}",
            "installments": 1
        }
        if WEBHOOK_URL and WEBHOOK_URL.startswith("https"):
            payment_data["notification_url"] = WEBHOOK_URL

        result = sdk.payment().create(payment_data)
        if result.get("status") in [200, 201]:
            payment = result["response"]
            pix_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            return {
                "qr_code": pix_data.get("qr_code"),
                "qr_code_base64": pix_data.get("qr_code_base64"),
                "expiration": payment.get("date_of_expiration"),
                "produto": nome_produto,
                "preco": preco_formatado,
                "payment_id": payment.get("id"),
                "produto_id": produto_id
            }
        return None
    except Exception as e:
        print(f"❌ Erro ao gerar PIX: {e}")
        return None

# ===============================
# FUNÇÕES DE ESTOQUE
# ===============================
def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return None
        
        if variacao_nome:
            if variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                itens = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
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
        if produto_id not in estoque_disponivel:
            return 0
        if variacao_nome and variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
            return len(estoque_disponivel[produto_id]["variacoes"][variacao_nome])
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# LOGS
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal: return None
        embed = discord.Embed(title="🛒 NOVO CARRINHO ATIVO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {"canal": canal.id, "mensagem_id": mensagem.id, "usuario": user.id, "produto": produto_nome}
        return mensagem
    except Exception as e:
        print(f"❌ Erro log: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos: return
        embed = discord.Embed(title="✅ PAGAMENTO CONFIRMADO", color=0x00ff88, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        if item_entregue:
            embed.add_field(name="🔐 Item Entregue", value=f"```{item_entregue}```", inline=False)
        await canal_pagos.send(embed=embed)
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
        print(f"❌ Erro log: {e}")

# ===============================
# DISCORD BOT
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands sincronizados")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")
        await self.change_presence(activity=discord.Game(name="G7 STORE 💎"))

bot = Bot()

# ===============================
# VIEWS E MODAIS
# ===============================
class CopiarPIXView(discord.ui.View):
    def __init__(self, codigo_pix: str):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix

    @discord.ui.button(label="📋 Copiar código PIX", style=discord.ButtonStyle.primary)
    async def copiar_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"```{self.codigo_pix}```", ephemeral=True)

class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
    chave = discord.ui.TextInput(label="Chave 2FA", placeholder="Cole sua chave aqui", min_length=16, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        totp = pyotp.TOTP(self.chave.value.strip().upper())
        codigo_atual = totp.now()
        tempo_restante = totp.interval - (int(time.time()) % totp.interval)
        embed = discord.Embed(title="🔐 CÓDIGO 2FA GERADO", color=0x00ff88)
        embed.add_field(name="📋 CÓDIGO:", value=f"```{codigo_atual}```", inline=False)
        embed.add_field(name="⏰ VÁLIDO POR:", value=f"{tempo_restante} segundos", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

class VariacoesView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list):
        super().__init__(timeout=300)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes
        options = [discord.SelectOption(label=v["nome"], description=f"R$ {v['preco']:.2f}", value=str(i)) for i, v in enumerate(variacoes)]
        select = discord.ui.Select(placeholder="Escolha uma opção...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            indice = int(interaction.data["values"][0])
            variacao = self.variacoes[indice]
            if verificar_estoque(self.produto_id, variacao["nome"]) == 0:
                await interaction.followup.send("❌ Esta opção está esgotada!", ephemeral=True)
                return
            pix_data = criar_pagamento_pix_com_preco(interaction.user.id, f"{self.produto_id}_{variacao['nome']}", variacao["preco"], f"{self.produto_nome} - {variacao['nome']}")
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento. Verifique se o Token MP está configurado.", ephemeral=True)
                return
            await log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data.get('payment_id', 'N/A'))
            embed_pix = discord.Embed(title="🧾 PAGAMENTO PIX", description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}", color=0x00ff88)
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            with BytesIO(qr_image_data) as image_binary:
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await interaction.user.send(embed=embed_pix, file=file, view=CopiarPIXView(pix_data["qr_code"]))
            await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro: {e}")
            await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list = None):
        super().__init__(timeout=None)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes or []
    
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success)
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.variacoes:
            await interaction.response.send_message("Escolha uma variação:", view=VariacoesView(self.produto_id, self.produto_nome, self.variacoes), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if verificar_estoque(self.produto_id) == 0:
            await interaction.followup.send("❌ Produto esgotado!", ephemeral=True)
            return
        pix_data = criar_pagamento_pix_com_preco(interaction.user.id, self.produto_id, produtos_disponiveis[self.produto_id]["preco"], self.produto_nome)
        if not pix_data:
            await interaction.followup.send("❌ Erro ao gerar pagamento. Verifique se o Token MP está configurado.", ephemeral=True)
            return
        await log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data.get('payment_id', 'N/A'))
        embed_pix = discord.Embed(title="🧾 PAGAMENTO PIX", description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}", color=0x00ff88)
        qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
        with BytesIO(qr_image_data) as image_binary:
            file = discord.File(fp=image_binary, filename="qrcode.png")
            await interaction.user.send(embed=embed_pix, file=file, view=CopiarPIXView(pix_data["qr_code"]))
        await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)

# ===============================
# COMANDOS ADMIN
# ===============================
@bot.tree.command(name="add_produto", description="[ADMIN] Adicionar novo produto")
async def add_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
        return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "tipo": "auto"}
    salvar_produtos(produtos_disponiveis)
    await interaction.response.send_message(f"✅ Produto {nome} adicionado!", ephemeral=True)

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
        return
    novos = [i.strip() for i in itens.split(",")]
    if produto_id not in estoque_disponivel: estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
    if variacao:
        if variacao not in estoque_disponivel[produto_id]["variacoes"]: estoque_disponivel[produto_id]["variacoes"][variacao] = []
        estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos)
    else:
        estoque_disponivel[produto_id]["itens"].extend(novos)
    salvar_estoque(estoque_disponivel)
    await interaction.response.send_message(f"✅ {len(novos)} itens adicionados!", ephemeral=True)

@bot.tree.command(name="configurar_produto", description="[ADMIN] Criar canal do produto")
async def configurar_produto(interaction: discord.Interaction, produto_id: str, nome_canal: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
        return
    prod = produtos_disponiveis[produto_id]
    canal = discord.utils.get(interaction.guild.channels, name=nome_canal) or await interaction.guild.create_text_channel(nome_canal)
    embed = discord.Embed(title=f"⚡ {prod['nome']}", description=f"**{prod['descricao']}**\n\n💰 Preço: R$ {prod['preco']:.2f}", color=0xffa500)
    if prod.get('imagem'): embed.set_image(url=prod['imagem'])
    await canal.purge(limit=5)
    await canal.send(embed=embed, view=ProdutoCompraView(produto_id, prod['nome'], prod.get('variacoes', [])))
    await interaction.followup.send(f"✅ Canal {canal.mention} configurado!", ephemeral=True)

# ===============================
# WEBHOOK FLASK
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 G7 STORE - Bot Online!", 200

@app.route('/webhook', methods=["POST"])
def webhook():
    data = request.json if request.is_json else {}
    payment_id = data.get('data', {}).get('id') or data.get('id')
    if not payment_id: return "OK", 200
    with webhook_lock:
        if str(payment_id) in pagamentos_processados: return "OK", 200
        try:
            payment_response = sdk.payment().get(payment_id)
            if payment_response["status"] == 200 and payment_response["response"]["status"] == "approved":
                payment = payment_response["response"]
                pagamentos_processados.add(str(payment_id))
                salvar_pagamentos_processados(pagamentos_processados)
                ref = payment.get("external_reference", "")
                partes = ref.split('_')
                if len(partes) >= 2:
                    user_id = int(partes[-2])
                    produto_id = partes[0]
                    future = asyncio.run_coroutine_threadsafe(bot.fetch_user(user_id), bot.loop)
                    user = future.result(timeout=10)
                    if user and produto_id in produtos_disponiveis:
                        item = entregar_do_estoque(produto_id)
                        if item:
                            asyncio.run_coroutine_threadsafe(user.send(f"✅ **{produtos_disponiveis[produto_id]['nome']}**\n\n```{item}```"), bot.loop)
                            asyncio.run_coroutine_threadsafe(log_pagamento_confirmado(user, produtos_disponiveis[produto_id]['nome'], payment.get("transaction_amount"), payment_id, item), bot.loop)
        except Exception as e: print(f"❌ Webhook error: {e}")
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# ===============================
# INICIAR
# ===============================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    bot.run(DISCORD_TOKEN)
