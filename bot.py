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
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # ← AGORA PEGA DO .ENV

ARQUIVO_PRODUTO = "produto.txt"
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

if os.path.exists(ARQUIVO_PRODUTO):
    print("📄 produto.txt encontrado")
else:
    print("⚠️ produto.txt não encontrado (opcional)")

# ========== CONFIGURAÇÕES DA LOJA (VOCÊ VAI PREENCHER DEPOIS) ==========
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
            return set(json.load(f))
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
            return json.load(f)
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
            return json.load(f)
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
if not MP_ACCESS_TOKEN:
    print("❌ MP_ACCESS_TOKEN não configurado no .env!")
    # sys.exit(1) # Não sai para o bot não morrer, mas avisa
else:
    sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
    print("💳 Mercado Pago SDK Inicializado")

def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    """Gera um pagamento PIX com logs detalhados para diagnóstico"""
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

        print(f"🔍 Tentando gerar PIX de R$ {preco_formatado} para o produto {produto_id}...")
        
        result = sdk.payment().create(payment_data)
        
        status_code = result.get("status")
        response_data = result.get("response")

        if status_code in [200, 201]:
            payment = response_data
            pix_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            
            print(f"✅ PIX Gerado com sucesso! ID: {payment.get('id')}")
            return {
                "qr_code": pix_data.get("qr_code"),
                "qr_code_base64": pix_data.get("qr_code_base64"),
                "expiration": payment.get("date_of_expiration"),
                "produto": nome_produto,
                "preco": preco_formatado,
                "payment_id": payment.get("id"),
                "produto_id": produto_id
            }
        else:
            print("\n" + "!"*30)
            print(f"❌ ERRO NA API DO MERCADO PAGO")
            print(f"Status Code: {status_code}")
            print(f"Resposta: {json.dumps(response_data, indent=2)}")
            print("!"*30 + "\n")
            return None

    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO CÓDIGO DE PAGAMENTO: {e}")
        import traceback
        traceback.print_exc()
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
        # self.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
        await self.tree.sync()

bot = MyBot()

# ===============================
# WEBHOOK SERVER (FLASK)
# ===============================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    global pagamentos_processados
    data = request.json
    print(f"📥 Webhook recebido: {data}")
    
    if data.get("action") == "payment.updated" or data.get("type") == "payment":
        payment_id = data.get("data", {}).get("id") or data.get("id")
        
        if payment_id:
            with webhook_lock:
                if str(payment_id) in pagamentos_processados:
                    print(f"⏭️ Pagamento {payment_id} já foi processado anteriormente.")
                    return "OK", 200
                
                asyncio.run_coroutine_threadsafe(verificar_pagamento_e_entregar(payment_id), bot.loop)
    
    return "OK", 200

async def verificar_pagamento_e_entregar(payment_id):
    global pagamentos_processados, estoque_disponivel
    
    try:
        print(f"🔎 Verificando pagamento {payment_id}...")
        payment_info = sdk.payment().get(payment_id)
        
        if payment_info["status"] != 200:
            return

        payment_data = payment_info["response"]
        status = payment_data.get("status")
        
        if status == "approved":
            external_ref = payment_data.get("external_reference", "")
            if not external_ref: return
            
            # Formato: produtoID_userID_timestamp
            parts = external_ref.split("_")
            if len(parts) < 2: return
            
            produto_id = parts[0]
            user_id = int(parts[1])
            
            with webhook_lock:
                if str(payment_id) in pagamentos_processados:
                    return
                
                pagamentos_processados.add(str(payment_id))
                salvar_pagamentos_processados(pagamentos_processados)

            # Entregar o produto
            await entregar_produto(user_id, produto_id, payment_id, payment_data.get("transaction_amount"))
            
    except Exception as e:
        print(f"❌ Erro ao processar pagamento {payment_id}: {e}")

async def entregar_produto(user_id, produto_id, payment_id, valor):
    global estoque_disponivel
    
    try:
        user = await bot.fetch_user(user_id)
        if not user: return

        produtos = carregar_produtos()
        nome_produto = produtos.get(produto_id, {}).get("nome", "Produto")

        with estoque_lock:
            estoque_disponivel = carregar_estoque()
            if produto_id in estoque_disponivel and len(estoque_disponivel[produto_id]) > 0:
                item_entregue = estoque_disponivel[produto_id].pop(0)
                salvar_estoque(estoque_disponivel)
                
                # Enviar DM
                embed = discord.Embed(
                    title="✅ Pagamento Confirmado!",
                    description=f"Obrigado por comprar na nossa loja!\n\n**Produto:** {nome_produto}\n**Valor:** R$ {valor}\n**ID Transação:** `{payment_id}`",
                    color=discord.Color.green()
                )
                embed.add_field(name="📦 Seu Produto:", value=f"```\n{item_entregue}\n```")
                
                try:
                    await user.send(embed=embed)
                except:
                    print(f"❌ Não consegui enviar DM para {user.name}")

                # Log no canal de vendas
                canal = bot.get_channel(CANAL_PAGOS)
                if canal:
                    log_embed = discord.Embed(
                        title="💰 Nova Venda!",
                        description=f"**Cliente:** {user.mention} (`{user.id}`)\n**Produto:** {nome_produto}\n**Valor:** R$ {valor}",
                        color=discord.Color.gold()
                    )
                    await canal.send(embed=log_embed)
            else:
                # Caso o estoque acabe bem na hora
                canal_log = bot.get_channel(CANAL_PAGOS)
                if canal_log:
                    await canal_log.send(f"⚠️ **ERRO CRÍTICO:** O usuário {user.mention} pagou por `{nome_produto}`, mas o estoque acabou!")
                
                try:
                    await user.send("⚠️ **Ocorreu um problema:** Seu pagamento foi aprovado, mas o estoque do produto acabou no exato momento. Por favor, entre em contato com o administrador.")
                except: pass

    except Exception as e:
        print(f"❌ Erro na entrega: {e}")

def run_flask():
    app.run(host="0.0.0.0", port=5000)

# ===============================
# COMANDOS DO BOT
# ===============================

@bot.tree.command(name="configurar", description="Configura um novo produto para venda")
async def configurar(interaction: discord.Interaction, id_produto: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return

    produtos = carregar_produtos()
    produtos[id_produto] = {"nome": nome, "preco": preco}
    salvar_produtos(produtos)
    
    await interaction.response.send_message(f"✅ Produto `{nome}` configurado com ID `{id_produto}` e preço `R$ {preco}`", ephemeral=True)

@bot.tree.command(name="estoque", description="Adiciona itens ao estoque")
async def estoque(interaction: discord.Interaction, id_produto: str, conteudo: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return

    global estoque_disponivel
    with estoque_lock:
        estoque_disponivel = carregar_estoque()
        if id_produto not in estoque_disponivel:
            estoque_disponivel[id_produto] = []
        
        # Pode adicionar vários separados por vírgula ou linha
        novos_itens = [i.strip() for i in conteudo.replace("\\n", "\n").split("\n") if i.strip()]
        estoque_disponivel[id_produto].extend(novos_itens)
        salvar_estoque(estoque_disponivel)

    await interaction.response.send_message(f"✅ Adicionados {len(novos_itens)} itens ao estoque de `{id_produto}`. Total: {len(estoque_disponivel[id_produto])}", ephemeral=True)

@bot.tree.command(name="comprar", description="Gera um pagamento para um produto")
async def comprar(interaction: discord.Interaction, id_produto: str):
    produtos = carregar_produtos()
    if id_produto not in produtos:
        await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
        return

    estoque = carregar_estoque()
    if id_produto not in estoque or len(estoque[id_produto]) == 0:
        await interaction.response.send_message("❌ Este produto está sem estoque no momento!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    nome_p = produtos[id_produto]["nome"]
    preco_p = produtos[id_produto]["preco"]

    pagamento = criar_pagamento_pix_com_preco(interaction.user.id, id_produto, preco_p, nome_p)

    if pagamento:
        # Criar Carrinho Ativo
        embed = discord.Embed(
            title=f"🛒 Carrinho - {nome_p}",
            description=f"Olá {interaction.user.mention}, seu pedido foi gerado!\n\n**Valor:** R$ {preco_p}\n\nEscaneie o QR Code ou copie o código abaixo para pagar via PIX.",
            color=discord.Color.blue()
        )
        
        # QR Code Base64 -> File
        qr_file = None
        if pagamento["qr_code_base64"]:
            img_data = base64.b64decode(pagamento["qr_code_base64"])
            qr_file = discord.File(BytesIO(img_data), filename="pix.png")
            embed.set_image(url="attachment://pix.png")

        await interaction.followup.send(embed=embed, file=qr_file)
        await interaction.followup.send(f"**Copia e Cola:**\n```\n{pagamento['qr_code']}\n```")

        # Log no canal de carrinhos
        canal_c = bot.get_channel(CANAL_CARRINHOS)
        if canal_c:
            log_c = discord.Embed(
                title="🛒 Novo Carrinho",
                description=f"**Usuário:** {interaction.user.mention}\n**Produto:** {nome_p}\n**Valor:** R$ {preco_p}",
                color=discord.Color.blue()
            )
            await canal_c.send(embed=log_c)
    else:
        await interaction.followup.send("❌ Erro ao gerar pagamento. Tente novamente mais tarde.")

@bot.tree.command(name="painel", description="Envia o painel de compras (Apenas Admin)")
async def painel(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        return

    produtos = carregar_produtos()
    if not produtos:
        await interaction.response.send_message("❌ Não há produtos configurados.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏪 Nossa Loja",
        description="Selecione um produto abaixo para comprar via PIX com entrega automática!",
        color=discord.Color.purple()
    )

    class Dropdown(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label=p["nome"], description=f"Preço: R$ {p['preco']}", value=pid)
                for pid, p in produtos.items()
            ]
            super().__init__(placeholder="Escolha um produto...", options=options)

        async def callback(self, interaction: discord.Interaction):
            id_p = self.values[0]
            # Reutiliza a lógica do comando comprar
            await comprar_logic(interaction, id_p)

    async def comprar_logic(inter, id_p):
        produtos = carregar_produtos()
        estoque = carregar_estoque()
        if id_p not in estoque or len(estoque[id_p]) == 0:
            await inter.response.send_message("❌ Sem estoque!", ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)
        pag = criar_pagamento_pix_com_preco(inter.user.id, id_p, produtos[id_p]["preco"], produtos[id_p]["nome"])
        
        if pag:
            emb = discord.Embed(title=f"🛒 Pedido: {produtos[id_p]['nome']}", description=f"Valor: R$ {produtos[id_p]['preco']}", color=discord.Color.blue())
            qr_f = None
            if pag["qr_code_base64"]:
                qr_f = discord.File(BytesIO(base64.b64decode(pag["qr_code_base64"])), filename="pix.png")
                emb.set_image(url="attachment://pix.png")
            await inter.followup.send(embed=emb, file=qr_f)
            await inter.followup.send(f"**PIX Copia e Cola:**\n```\n{pag['qr_code']}\n```")
        else:
            await inter.followup.send("❌ Erro ao gerar PIX.")

    view = discord.ui.View()
    view.add_item(Dropdown())
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Painel enviado!", ephemeral=True)

# ===============================
# START
# ===============================
if __name__ == "__main__":
    # Rodar Flask em uma thread separada
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    # Rodar Bot
    bot.run(DISCORD_TOKEN)
