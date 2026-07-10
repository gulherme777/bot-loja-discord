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
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

ARQUIVO_PRODUTO = "produto.txt"
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

if os.path.exists(ARQUIVO_PRODUTO):
    print("📄 produto.txt encontrado")
else:
    print("⚠️ produto.txt não encontrado (opcional)")

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
# SISTEMA DE PAGAMENTOS PROCESSADOS
# ===============================
def carregar_pagamentos_processados():
    if os.path.exists(ARQUIVO_PAGAMENTOS_PROCESSADOS):
        with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'r', encoding='utf-8') as f:
            try:
                return set(json.load(f))
            except:
                return set()
    return set()

def salvar_pagamentos_processados(pagamentos):
    with open(ARQUIVO_PAGAMENTOS_PROCESSADOS, 'w', encoding='utf-8') as f:
        json.dump(list(pagamentos), f, indent=2)

pagamentos_processados = carregar_pagamentos_processados()
print(f"🔒 {len(pagamentos_processados)} pagamentos já processados")

# ===============================
# SISTEMA DE ESTOQUE
# ===============================
def carregar_estoque():
    if os.path.exists(ARQUIVO_ESTOQUE_JSON):
        with open(ARQUIVO_ESTOQUE_JSON, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                return {}
    else:
        estoque_vazio = {}
        salvar_estoque(estoque_vazio)
        return estoque_vazio

def salvar_estoque(estoque):
    with open(ARQUIVO_ESTOQUE_JSON, 'w', encoding='utf-8') as f:
        json.dump(estoque, f, indent=2, ensure_ascii=False)

estoque_disponivel = carregar_estoque()
print(f"📦 Estoque carregado")

# ===============================
# SISTEMA DE GERENCIAMENTO DE PRODUTOS
# ===============================
def carregar_produtos():
    if os.path.exists(ARQUIVO_PRODUTOS_JSON):
        with open(ARQUIVO_PRODUTOS_JSON, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                return {}
    else:
        produtos_vazio = {}
        salvar_produtos(produtos_vazio)
        return produtos_vazio

def salvar_produtos(produtos):
    with open(ARQUIVO_PRODUTOS_JSON, 'w', encoding='utf-8') as f:
        json.dump(produtos, f, indent=2, ensure_ascii=False)

# ===============================
# CONFIG MERCADO PAGO
# ===============================
sdk = None
if not MP_ACCESS_TOKEN:
    print("❌ MP_ACCESS_TOKEN não configurado!")
else:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    print("💳 Mercado Pago SDK Inicializado")

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
# CONFIG DISCORD BOT
# ===============================
class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

# ===============================
# WEBHOOK SERVER (FLASK)
# ===============================
flask_app = Flask(__name__) # Nome alterado para flask_app para compatibilidade com render_start.py

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    global pagamentos_processados
    data = request.json
    if data.get("action") == "payment.updated" or data.get("type") == "payment":
        payment_id = data.get("data", {}).get("id") or data.get("id")
        if payment_id:
            with webhook_lock:
                if str(payment_id) in pagamentos_processados:
                    return "OK", 200
                asyncio.run_coroutine_threadsafe(verificar_pagamento_e_entregar(payment_id), bot.loop)
    return "OK", 200

async def verificar_pagamento_e_entregar(payment_id):
    if not sdk: return
    try:
        payment_info = sdk.payment().get(payment_id)
        if payment_info["status"] == 200:
            payment_data = payment_info["response"]
            if payment_data.get("status") == "approved":
                external_ref = payment_data.get("external_reference", "")
                parts = external_ref.split("_")
                if len(parts) >= 2:
                    produto_id, user_id = parts[0], int(parts[1])
                    with webhook_lock:
                        if str(payment_id) not in pagamentos_processados:
                            pagamentos_processados.add(str(payment_id))
                            salvar_pagamentos_processados(pagamentos_processados)
                            await entregar_produto(user_id, produto_id, payment_id, payment_data.get("transaction_amount"))
    except Exception as e:
        print(f"❌ Erro webhook: {e}")

async def entregar_produto(user_id, produto_id, payment_id, valor):
    try:
        user = await bot.fetch_user(user_id)
        produtos = carregar_produtos()
        nome_produto = produtos.get(produto_id, {}).get("nome", "Produto")
        with estoque_lock:
            estoque = carregar_estoque()
            if produto_id in estoque and len(estoque[produto_id]) > 0:
                item = estoque[produto_id].pop(0)
                salvar_estoque(estoque)
                embed = discord.Embed(title="✅ Pagamento Confirmado!", color=discord.Color.green())
                embed.add_field(name="📦 Seu Produto:", value=f"```\n{item}\n```")
                try: await user.send(embed=embed)
                except: pass
                canal = bot.get_channel(CANAL_PAGOS)
                if canal: await canal.send(embed=discord.Embed(title="💰 Nova Venda!", description=f"Cliente: {user.mention}\nProduto: {nome_produto}\nValor: R$ {valor}", color=discord.Color.gold()))
            else:
                canal = bot.get_channel(CANAL_PAGOS)
                if canal: await canal.send(f"⚠️ Erro: Estoque vazio para {user.mention} - {nome_produto}")
    except Exception as e:
        print(f"❌ Erro entrega: {e}")

# ===============================
# COMANDOS
# ===============================
@bot.tree.command(name="configurar")
async def configurar(interaction: discord.Interaction, id_produto: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return
    produtos = carregar_produtos()
    produtos[id_produto] = {"nome": nome, "preco": preco}
    salvar_produtos(produtos)
    await interaction.response.send_message(f"✅ Configurado: {nome}", ephemeral=True)

@bot.tree.command(name="estoque")
async def estoque(interaction: discord.Interaction, id_produto: str, conteudo: str):
    if interaction.user.id != MEU_ID: return
    with estoque_lock:
        estoque = carregar_estoque()
        if id_produto not in estoque: estoque[id_produto] = []
        novos = [i.strip() for i in conteudo.replace("\\n", "\n").split("\n") if i.strip()]
        estoque[id_produto].extend(novos)
        salvar_estoque(estoque)
    await interaction.response.send_message(f"✅ Estoque atualizado para {id_produto}", ephemeral=True)

@bot.tree.command(name="comprar")
async def comprar(interaction: discord.Interaction, id_produto: str):
    produtos = carregar_produtos()
    if id_produto not in produtos:
        await interaction.response.send_message("❌ Produto não encontrado", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    pag = criar_pagamento_pix_com_preco(interaction.user.id, id_produto, produtos[id_produto]["preco"], produtos[id_produto]["nome"])
    if pag:
        embed = discord.Embed(title=f"🛒 {produtos[id_produto]['nome']}", description=f"Valor: R$ {produtos[id_produto]['preco']}", color=discord.Color.blue())
        qr_file = None
        if pag["qr_code_base64"]:
            qr_file = discord.File(BytesIO(base64.b64decode(pag["qr_code_base64"])), filename="pix.png")
            embed.set_image(url="attachment://pix.png")
        await interaction.followup.send(embed=embed, file=qr_file)
        await interaction.followup.send(f"**Copia e Cola:**\n```\n{pag['qr_code']}\n```")
        canal = bot.get_channel(CANAL_CARRINHOS)
        if canal: await canal.send(embed=discord.Embed(title="🛒 Novo Carrinho", description=f"Usuário: {interaction.user.mention}\nProduto: {produtos[id_produto]['nome']}", color=discord.Color.blue()))
    else:
        await interaction.followup.send("❌ Erro ao gerar pagamento")

@bot.tree.command(name="painel")
async def painel(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return
    produtos = carregar_produtos()
    if not produtos: return
    embed = discord.Embed(title="🏪 Loja", description="Selecione um produto", color=discord.Color.purple())
    class Dropdown(discord.ui.Select):
        def __init__(self):
            options = [discord.SelectOption(label=p["nome"], value=pid) for pid, p in produtos.items()]
            super().__init__(placeholder="Escolha...", options=options)
        async def callback(self, interaction: discord.Interaction):
            await comprar_logic(interaction, self.values[0])
    async def comprar_logic(inter, id_p):
        await inter.response.defer(ephemeral=True)
        pag = criar_pagamento_pix_com_preco(inter.user.id, id_p, produtos[id_p]["preco"], produtos[id_p]["nome"])
        if pag:
            emb = discord.Embed(title=f"🛒 {produtos[id_p]['nome']}", color=discord.Color.blue())
            qr_f = None
            if pag["qr_code_base64"]:
                qr_f = discord.File(BytesIO(base64.b64decode(pag["qr_code_base64"])), filename="pix.png")
                emb.set_image(url="attachment://pix.png")
            await inter.followup.send(embed=emb, file=qr_f)
            await inter.followup.send(f"**PIX:**\n```\n{pag['qr_code']}\n```")
    view = discord.ui.View()
    view.add_item(Dropdown())
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Painel enviado", ephemeral=True)

if __name__ == "__main__":
    # Local run logic
    t = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=5000))
    t.daemon = True
    t.start()
    bot.run(DISCORD_TOKEN)
