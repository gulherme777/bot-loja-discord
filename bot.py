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

print("🔧 Iniciando bot da NOVA LOJA...")

# ===============================
# CONFIG (TUDO VIA VARIÁVEIS DE AMBIENTE)
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", os.environ.get("DISCORD_TOKEN_G7", ""))
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://bot-discord-loja-eg7u.onrender.com/webhook")

ARQUIVO_PRODUTO = "produto.txt"
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

# ========== CONFIGURAÇÕES DA LOJA ==========
GUILD_ID = 1513768859838971924
CANAL_CARRINHOS = 1521749470075682856
CANAL_PAGOS = 1521749470075682859
MEU_ID = 1286512677958713344
CARGO_ADMIN = 1286512677958713344

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
        with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'r', encoding='utf-8') as f:
            try: return set(json.load(f))
            except: return set()
    return set()

def salvar_pagamentos_processados(pagamentos):
    with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'w', encoding='utf-8') as f:
        json.dump(list(pagamentos), f, indent=2)

def carregar_estoque():
    if os.path.exists(ARQUIVO_ESTOQUE_JSON):
        with open(ARQUIVO_ESTOQUE_JSON, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except: return {}
    return {}

def salvar_estoque(estoque):
    with open(ARQUIVO_ESTOQUE_JSON, 'w', encoding='utf-8') as f:
        json.dump(estoque, f, indent=2, ensure_ascii=False)

def carregar_produtos():
    if os.path.exists(ARQUIVO_PRODUTOS_JSON):
        with open(ARQUIVO_PRODUTOS_JSON, 'r', encoding='utf-8') as f:
            try: return json.load(f)
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
sdk = mercadopago.SDK(MP_ACCESS_TOKEN) if MP_ACCESS_TOKEN else None

def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    if not sdk: return None
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
            payment = result.get("response")
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
    except Exception as e:
        print(f"❌ Erro pagamento: {e}")
    return None

# ===============================
# LÓGICA DE ESTOQUE E ENTREGA
# ===============================
def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return None
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
        if produto_id not in estoque_disponivel: return 0
        if variacao_nome:
            return len(estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, []))
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# LOGS E INTERFACE
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal: return
        embed = discord.Embed(title="🛒 NOVO CARRINHO ATIVO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {"canal": canal.id, "mensagem_id": mensagem.id, "usuario": user.id, "produto": produto_nome}
    except Exception as e: print(f"❌ Erro log carrinho: {e}")

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(title="✅ PAGAMENTO CONFIRMADO", color=0x00ff88, timestamp=datetime.now())
            embed.add_field(name="Cliente", value=user.mention, inline=True)
            embed.add_field(name="Produto", value=produto_nome, inline=True)
            embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
            if item_entregue: embed.add_field(name="🔐 Item Entregue", value=f"```{item_entregue}```", inline=False)
            await canal_pagos.send(embed=embed)
        
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    emb = discord.Embed(title="✅ PAGAMENTO APROVADO", description=f"Cliente: {user.mention}\nProduto: {produto_nome}\nValor: R$ {valor:.2f}", color=0x00ff88, timestamp=datetime.now())
                    await msg.edit(embed=emb)
                except: pass
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e: print(f"❌ Erro log pagos: {e}")

# ===============================
# CLASSES UI (BOTÕES, MODAIS, VIEWS)
# ===============================
class CopiarPIXView(discord.ui.View):
    def __init__(self, codigo_pix: str):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix
    @discord.ui.button(label="📋 Copiar código PIX", style=discord.ButtonStyle.primary)
    async def copiar_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"```{self.codigo_pix}```", ephemeral=True)

class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
    chave = discord.ui.TextInput(label="Chave 2FA", placeholder="Cole sua chave aqui...", min_length=16, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            totp = pyotp.TOTP(self.chave.value.strip().upper())
            codigo = totp.now()
            embed = discord.Embed(title="🔐 CÓDIGO 2FA GERADO", description=f"📋 **CÓDIGO:** ```{codigo}```", color=0x00ff88)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e: await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

class Canal2FAView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🔐 Gerar Código 2FA", style=discord.ButtonStyle.success, custom_id="btn_gerar_2fa")
    async def gerar_2fa_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(Modal2FA())

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
        v = self.variacoes[int(interaction.data["values"][0])]
        if verificar_estoque(self.produto_id, v["nome"]) == 0 and produtos_disponiveis[self.produto_id].get("tipo") == "auto":
            await interaction.followup.send("❌ Esgotado!", ephemeral=True); return
        pag = criar_pagamento_pix_com_preco(interaction.user.id, f"{self.produto_id}_{v['nome']}", v["preco"], f"{self.produto_nome} - {v['nome']}")
        if pag:
            await log_carrinho_ativo(interaction.user, pag['produto'], pag['preco'], pag['payment_id'])
            emb = discord.Embed(title="🧾 PAGAMENTO PIX", description=f"**Produto:** {pag['produto']}\n**Valor:** R$ {pag['preco']:.2f}", color=0x00ff88)
            file = discord.File(BytesIO(base64.b64decode(pag["qr_code_base64"])), filename="qr.png")
            emb.set_image(url="attachment://qr.png")
            await interaction.user.send(embed=emb, file=file, view=CopiarPIXView(pag["qr_code"]))
            await interaction.followup.send("📨 Enviado no privado!", ephemeral=True)

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list = None):
        super().__init__(timeout=None)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes or []
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_comprar")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.variacoes:
            await interaction.response.send_message("Selecione a opção:", view=VariacoesView(self.produto_id, self.produto_nome, self.variacoes), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        p = produtos_disponiveis[self.produto_id]
        if verificar_estoque(self.produto_id) == 0 and p.get("tipo") == "auto":
            await interaction.followup.send("❌ Esgotado!", ephemeral=True); return
        pag = criar_pagamento_pix_com_preco(interaction.user.id, self.produto_id, p["preco"], p["nome"])
        if pag:
            await log_carrinho_ativo(interaction.user, pag['produto'], pag['preco'], pag['payment_id'])
            emb = discord.Embed(title="🧾 PAGAMENTO PIX", description=f"**Produto:** {pag['produto']}\n**Valor:** R$ {pag['preco']:.2f}", color=0x00ff88)
            file = discord.File(BytesIO(base64.b64decode(pag["qr_code_base64"])), filename="qr.png")
            emb.set_image(url="attachment://qr.png")
            await interaction.user.send(embed=emb, file=file, view=CopiarPIXView(pag["qr_code"]))
            await interaction.followup.send("📨 Enviado no privado!", ephemeral=True)

# ===============================
# DISCORD BOT SETUP
# ===============================
class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
    async def setup_hook(self): await self.tree.sync()

bot = MyBot()

# ===============================
# COMANDOS ADMIN
# ===============================
@bot.tree.command(name="criar_produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "auto"):
    if interaction.user.id != MEU_ID: return
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "tipo": tipo, "imagem": "", "variacoes": []}
    salvar_produtos(produtos_disponiveis)
    if id not in estoque_disponivel: estoque_disponivel[id] = {"itens": [], "variacoes": {}}
    salvar_estoque(estoque_disponivel)
    await interaction.response.send_message(f"✅ Produto `{nome}` criado!", ephemeral=True)

@bot.tree.command(name="add_estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    with estoque_lock:
        if produto_id not in estoque_disponivel: estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
        if variacao:
            if variacao not in estoque_disponivel[produto_id]["variacoes"]: estoque_disponivel[produto_id]["variacoes"][variacao] = []
            estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos)
        else: estoque_disponivel[produto_id]["itens"].extend(novos)
        salvar_estoque(estoque_disponivel)
    await interaction.response.send_message(f"✅ {len(novos)} itens adicionados!", ephemeral=True)

@bot.tree.command(name="configurar_produto")
async def configurar_produto(interaction: discord.Interaction, produto_id: str, nome_canal: str):
    if interaction.user.id != MEU_ID: return
    await interaction.response.defer(ephemeral=True)
    p = produtos_disponiveis.get(produto_id)
    if not p: await interaction.followup.send("❌ Não existe!"); return
    canal = discord.utils.get(interaction.guild.channels, name=nome_canal) or await interaction.guild.create_text_channel(nome_canal)
    emb = discord.Embed(title=f"⚡ {p['nome']}", description=p['descricao'].replace('|', '\n✅ '), color=0xffa500)
    if p.get('imagem'): emb.set_image(url=p['imagem'])
    emb.add_field(name="💰 Valor", value=f"R$ {p['preco']:.2f}")
    await canal.purge(limit=5)
    await canal.send(embed=emb, view=ProdutoCompraView(produto_id, p['nome'], p.get('variacoes')))
    await interaction.followup.send("✅ Configurado!", ephemeral=True)

@bot.tree.command(name="add_variacao")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return
    if produto_id in produtos_disponiveis:
        produtos_disponiveis[produto_id].setdefault("variacoes", []).append({"nome": nome, "preco": preco})
        salvar_produtos(produtos_disponiveis)
        await interaction.response.send_message(f"✅ Variação `{nome}` adicionada!", ephemeral=True)

@bot.tree.command(name="configurar_2fa")
async def configurar_2fa(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    emb = discord.Embed(title="🔐 GERADOR 2FA", description="Clique abaixo para gerar seu código.", color=0x00ff88)
    await interaction.channel.send(embed=emb, view=Canal2FAView())
    await interaction.response.send_message("✅ 2FA Configurado!", ephemeral=True)

@bot.tree.command(name="set_imagem")
async def set_imagem(interaction: discord.Interaction, produto_id: str, url: str):
    if interaction.user.id != MEU_ID: return
    if produto_id in produtos_disponiveis:
        produtos_disponiveis[produto_id]["imagem"] = url
        salvar_produtos(produtos_disponiveis)
        await interaction.response.send_message("✅ Imagem definida!", ephemeral=True)

# ===============================
# WEBHOOK SERVER
# ===============================
flask_app = Flask(__name__)

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or request.form.to_dict()
    pid = data.get('data', {}).get('id') or data.get('id')
    if pid:
        with webhook_lock:
            if str(pid) not in pagamentos_processados:
                asyncio.run_coroutine_threadsafe(processar_pagamento(pid), bot.loop)
    return "OK", 200

async def processar_pagamento(payment_id):
    try:
        res = sdk.payment().get(payment_id)
        if res["status"] == 200 and res["response"]["status"] == "approved":
            p_data = res["response"]
            ref = p_data.get("external_reference", "")
            partes = ref.split('_')
            if len(partes) >= 3:
                user_id = int(partes[-2])
                prod_id = partes[0]
                var_nome = partes[1] if len(partes) == 4 else None
                
                with webhook_lock:
                    pagamentos_processados.add(str(payment_id))
                    salvar_pagamentos_processados(pagamentos_processados)
                
                user = await bot.fetch_user(user_id)
                p_info = produtos_disponiveis.get(prod_id)
                if user and p_info:
                    if p_info.get("tipo") == "auto":
                        item = entregar_do_estoque(prod_id, var_nome)
                        if item:
                            await user.send(f"✅ **Pago!**\n📦 **{p_info['nome']}**\n🔐 **Produto:**\n```{item}```")
                            await log_pagamento_confirmado(user, p_info['nome'], p_data.get('transaction_amount', 0), payment_id, item)
                        else:
                            await user.send("✅ **Pago!**\n⚠️ Estoque vazio, um admin entregará em breve.")
                    else:
                        await user.send("✅ **Pago!**\n⏳ Entrega manual, aguarde um admin.")
    except Exception as e: print(f"❌ Erro webhook proc: {e}")

# ===============================
# START
# ===============================
if __name__ == "__main__":
    t = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=5000))
    t.daemon = True
    t.start()
    bot.run(DISCORD_TOKEN)
