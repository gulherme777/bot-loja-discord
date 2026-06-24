# -*- coding: utf-8 -*-
import discord
from discord import app_commands
import aiohttp
from flask import Flask, request
import threading
import asyncio
import os
import json
import time
from datetime import datetime

print("🔥 INICIANDO BOT - VERSÃO URGENTE")

# ===============================
# CONFIGURAÇÕES
# ===============================
TOKEN = os.environ.get("DISCORD_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    WEBHOOK_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"

CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
ADMIN_ID = 1431125477069688953
INFINITE_TAG = "guilherme_vinicius90"
PORT = int(os.environ.get("PORT", 10000))

# ===============================
# DADOS
# ===============================
produtos = {}
estoque = {}
pagamentos = set()
carrinhos = {}

# ===============================
# FUNÇÕES DE ARQUIVO
# ===============================
def salvar_dados():
    try:
        with open("produtos.json", "w") as f:
            json.dump(produtos, f, indent=2)
        with open("estoque.json", "w") as f:
            json.dump(estoque, f, indent=2)
        with open("pagamentos.json", "w") as f:
            json.dump(list(pagamentos), f, indent=2)
        print("✅ Dados salvos")
    except Exception as e:
        print(f"❌ Erro ao salvar: {e}")

def carregar_dados():
    global produtos, estoque, pagamentos
    try:
        if os.path.exists("produtos.json"):
            with open("produtos.json", "r") as f:
                produtos = json.load(f)
        if os.path.exists("estoque.json"):
            with open("estoque.json", "r") as f:
                estoque = json.load(f)
        if os.path.exists("pagamentos.json"):
            with open("pagamentos.json", "r") as f:
                pagamentos = set(json.load(f))
        print(f"✅ Dados carregados: {len(produtos)} produtos")
    except Exception as e:
        print(f"❌ Erro ao carregar: {e}")

carregar_dados()

# ===============================
# BOT
# ===============================
class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
    
    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Comandos sincronizados")
    
    async def on_ready(self):
        print(f"🟢 Bot: {self.user}")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{len(produtos)} produtos"
        ))

bot = Bot()

# ===============================
# FUNÇÃO DE ENTREGA - A MAIS IMPORTANTE!
# ===============================
async def entregar_produto(usuario_id, produto_id, variacao, valor, slug):
    """ENTREGA O PRODUTO PARA O CLIENTE - ESSA É A FUNÇÃO PRINCIPAL"""
    try:
        print(f"🚀 INICIANDO ENTREGA para {usuario_id}")
        
        # Buscar usuário
        usuario = await bot.fetch_user(usuario_id)
        print(f"👤 Usuário: {usuario.name}")
        
        # Buscar produto
        produto = produtos.get(produto_id)
        if not produto:
            print(f"❌ Produto {produto_id} não encontrado")
            await usuario.send("❌ Produto não encontrado! Contate o admin.")
            return
        
        # Nome do produto
        if variacao and variacao != "NONE":
            nome_produto = f"{produto['nome']} ({variacao})"
        else:
            nome_produto = produto['nome']
        
        print(f"📦 Produto: {nome_produto}")
        
        # PEGAR ITEM DO ESTOQUE
        item = None
        if produto_id in estoque:
            if variacao and variacao != "NONE":
                # Variação
                if variacao in estoque[produto_id].get("variacoes", {}):
                    lista = estoque[produto_id]["variacoes"][variacao]
                    if lista:
                        item = lista.pop(0)
                        print(f"✅ Item da variação: {item}")
            else:
                # Simples
                if "itens" in estoque[produto_id]:
                    lista = estoque[produto_id]["itens"]
                    if lista:
                        item = lista.pop(0)
                        print(f"✅ Item simples: {item}")
        
        # SALVAR ESTOQUE
        salvar_dados()
        
        # NOTIFICAR CANAL DE PAGAMENTOS
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="✅ PAGAMENTO CONFIRMADO",
                color=0x00ff88,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Cliente", value=f"{usuario.mention}", inline=False)
            embed.add_field(name="📦 Produto", value=nome_produto, inline=True)
            embed.add_field(name="💰 Valor", value=f"R$ {valor:.2f}", inline=True)
            if item:
                embed.add_field(name="🔐 Item", value=f"```{item}```", inline=False)
            else:
                embed.add_field(name="⚠️ STATUS", value="```ESTOQUE ESGOTADO - ENTREGA MANUAL```", inline=False)
            embed.set_footer(text=f"ID: {slug}")
            await canal_pagos.send(embed=embed)
            print("✅ Notificação enviada ao canal de pagamentos")
        
        # ATUALIZAR CARRINHO
        if slug in carrinhos:
            canal_carrinhos = bot.get_channel(CANAL_CARRINHOS)
            if canal_carrinhos:
                try:
                    msg = await canal_carrinhos.fetch_message(carrinhos[slug])
                    embed = discord.Embed(
                        title="✅ CARRINHO FINALIZADO",
                        description=f"Cliente {usuario.mention} pagou!",
                        color=0x00ff88,
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Produto", value=nome_produto)
                    await msg.edit(embed=embed)
                    print("✅ Carrinho atualizado")
                except Exception as e:
                    print(f"❌ Erro no carrinho: {e}")
            del carrinhos[slug]
        
        # ENTREGAR PARA O CLIENTE - A PARTE MAIS IMPORTANTE!
        if item:
            print(f"📨 ENTREGANDO para {usuario.name}")
            embed = discord.Embed(
                title="🎁 **SUA ENTREGA CHEGOU!**",
                description="Seu pagamento foi confirmado!",
                color=0x00ff88,
                timestamp=datetime.now()
            )
            embed.add_field(name="📦 Produto", value=f"```{nome_produto}```", inline=False)
            embed.add_field(name="🔐 SEU CÓDIGO", value=f"```\n{item}\n```", inline=False)
            embed.set_footer(text="Obrigado pela compra! ❤️")
            
            await usuario.send(embed=embed)
            print(f"✅ ENTREGA REALIZADA COM SUCESSO para {usuario.name}")
        else:
            print(f"⚠️ ESTOQUE ESGOTADO para {nome_produto}")
            # Avisar admin
            admin = await bot.fetch_user(ADMIN_ID)
            await admin.send(f"🚨 ESTOQUE ESGOTADO!\nCliente: {usuario.mention}\nProduto: {nome_produto}\nSlug: {slug}")
            
            # Avisar cliente
            await usuario.send(
                f"✅ Pagamento confirmado para **{nome_produto}**!\n"
                f"⚠️ O estoque acabou, mas o admin vai te entregar em breve!"
            )
            
    except Exception as e:
        print(f"❌ ERRO FATAL NA ENTREGA: {e}")
        import traceback
        traceback.print_exc()

# ===============================
# WEBHOOK FLASK
# ===============================
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        print(f"📨 Webhook: {data}")
        
        if not data or data.get('status') != 'paid':
            return "OK", 200
        
        slug = data.get('invoice_slug')
        print(f"✅ Pagamento: {slug}")
        
        # Verificar duplicado
        if slug in pagamentos:
            print(f"⏭️ Já processado: {slug}")
            return "OK", 200
        
        pagamentos.add(slug)
        salvar_dados()
        
        # Pegar dados do NSU
        nsu = data.get('order_nsu', '')
        parts = nsu.split('|')
        
        if len(parts) < 3:
            print(f"❌ NSU inválido: {nsu}")
            return "OK", 200
        
        produto_id = parts[0]
        usuario_id = int(parts[1])
        variacao = parts[2] if len(parts) > 2 else "NONE"
        valor = float(data.get('amount', 0)) / 100
        
        print(f"📦 Processando: {produto_id} | {usuario_id} | {variacao}")
        
        # CHAMAR A ENTREGA - AQUI É ONDE ACONTECE!
        asyncio.run_coroutine_threadsafe(
            entregar_produto(usuario_id, produto_id, variacao, valor, slug),
            bot.loop
        )
        
        print(f"✅ Entrega agendada para {usuario_id}")
        return "OK", 200
        
    except Exception as e:
        print(f"❌ Erro webhook: {e}")
        import traceback
        traceback.print_exc()
        return "OK", 200

@flask_app.route('/', methods=['GET'])
def home():
    return "🟢 Bot online!", 200

# ===============================
# COMANDOS
# ===============================

@bot.tree.command(name="criar_produto", description="[ADMIN] Criar produto")
async def criar(interaction: discord.Interaction, id: str, nome: str, preco: float, desc: str):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)
    
    produtos[id] = {"nome": nome, "preco": preco, "descricao": desc, "variacoes": []}
    if id not in estoque:
        estoque[id] = {"itens": [], "variacoes": {}}
    salvar_dados()
    
    await interaction.response.send_message(f"✅ Produto `{id}` criado!", ephemeral=True)

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)
    
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    
    if produto_id not in estoque:
        estoque[produto_id] = {"itens": [], "variacoes": {}}
    
    if variacao:
        if "variacoes" not in estoque[produto_id]:
            estoque[produto_id]["variacoes"] = {}
        if variacao not in estoque[produto_id]["variacoes"]:
            estoque[produto_id]["variacoes"][variacao] = []
        estoque[produto_id]["variacoes"][variacao].extend(novos)
    else:
        estoque[produto_id]["itens"].extend(novos)
    
    salvar_dados()
    await interaction.response.send_message(f"✅ {len(novos)} itens adicionados!", ephemeral=True)

@bot.tree.command(name="sincronizar", description="[ADMIN] Enviar painel")
async def sincronizar(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)
    
    produto = produtos.get(produto_id)
    if not produto:
        return await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
    
    total = 0
    if produto_id in estoque:
        total += len(estoque[produto_id].get("itens", []))
        for v in estoque[produto_id].get("variacoes", {}).values():
            total += len(v)
    
    embed = discord.Embed(
        title=f"🛒 {produto['nome']}",
        description=f"{produto['descricao']}\n\n📦 Estoque: **{total}**",
        color=0x5865F2
    )
    embed.add_field(name="💰 Preço", value=f"R$ {produto['preco']:.2f}")
    
    view = discord.ui.View()
    btn = discord.ui.Button(label="🛒 COMPRAR", style=discord.ButtonStyle.success)
    
    async def comprar(inter: discord.Interaction):
        p = produtos.get(produto_id)
        if not p:
            return await inter.response.send_message("❌ Produto não encontrado!", ephemeral=True)
        
        if p.get("variacoes"):
            select = discord.ui.Select(placeholder="Selecione a variação...")
            for v in p["variacoes"]:
                select.add_option(label=v["nome"], value=v["nome"], description=f"R$ {v['preco']:.2f}")
            
            async def sel_callback(s_inter: discord.Interaction):
                var = select.values[0]
                preco_var = None
                for v in p["variacoes"]:
                    if v["nome"] == var:
                        preco_var = v["preco"]
                        break
                if preco_var:
                    await gerar_link(s_inter, produto_id, preco_var, f"{p['nome']} ({var})", var)
            
            select.callback = sel_callback
            v_view = discord.ui.View()
            v_view.add_item(select)
            await inter.response.send_message("Selecione:", view=v_view, ephemeral=True)
        else:
            await gerar_link(inter, produto_id, p["preco"], p["nome"], "NONE")
    
    btn.callback = comprar
    view.add_item(btn)
    
    await canal.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Painel enviado!", ephemeral=True)

async def gerar_link(inter, p_id, preco, nome, var):
    await inter.response.defer(ephemeral=True)
    
    nsu = f"{p_id}|{inter.user.id}|{var}|{int(time.time())}"
    
    payload = {
        "handle": INFINITE_TAG,
        "order_nsu": nsu,
        "items": [{"quantity": 1, "price": int(preco * 100), "description": nome[:60]}]
    }
    if WEBHOOK_URL:
        payload["webhook_url"] = WEBHOOK_URL
        print(f"📡 Webhook: {WEBHOOK_URL}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.checkout.infinitepay.io/links", json=payload) as resp:
                if resp.status in [200, 201]:
                    data = await resp.json()
                    url = data.get("url")
                    slug = data.get("invoice_slug")
                    
                    # Salvar carrinho
                    canal_c = bot.get_channel(CANAL_CARRINHOS)
                    if canal_c:
                        embed = discord.Embed(
                            title="🛒 NOVO CARRINHO",
                            color=0xffaa00,
                            timestamp=datetime.now()
                        )
                        embed.add_field(name="Cliente", value=inter.user.mention)
                        embed.add_field(name="Produto", value=nome)
                        embed.add_field(name="Valor", value=f"R$ {preco:.2f}")
                        msg = await canal_c.send(embed=embed)
                        carrinhos[slug] = msg.id
                    
                    # Enviar pro cliente
                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(label="💳 PAGAR", style=discord.ButtonStyle.link, url=url))
                    
                    await inter.user.send(
                        f"✅ Link para **{nome}** gerado!",
                        view=view
                    )
                    await inter.followup.send("📨 Link enviado no seu privado!", ephemeral=True)
                else:
                    await inter.followup.send("❌ Erro ao gerar link.", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro: {e}")
        await inter.followup.send("❌ Erro ao gerar link.", ephemeral=True)

@bot.tree.command(name="testar", description="[ADMIN] Testar entrega")
async def testar(interaction: discord.Interaction, produto_id: str, usuario: discord.User):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)
    
    await interaction.response.send_message(f"🔄 Testando entrega para {usuario.mention}...", ephemeral=True)
    
    # Simular webhook
    await entregar_produto(
        usuario.id,
        produto_id,
        "NONE",
        10.00,
        "teste_manual"
    )

@bot.tree.command(name="estoque", description="[ADMIN] Ver estoque")
async def ver_estoque(interaction: discord.Interaction, produto_id: str):
    if interaction.user.id != ADMIN_ID:
        return await interaction.response.send_message("❌ Sem permissão!", ephemeral=True)
    
    if produto_id not in produtos:
        return await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
    
    total = 0
    if produto_id in estoque:
        total += len(estoque[produto_id].get("itens", []))
        for v in estoque[produto_id].get("variacoes", {}).values():
            total += len(v)
    
    await interaction.response.send_message(f"📦 Estoque de `{produto_id}`: **{total}** itens", ephemeral=True)

@bot.tree.command(name="ping", description="Verificar latência")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! Latência: {round(bot.latency * 1000)}ms")

# ===============================
# INICIAR
# ===============================
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("✅ Flask iniciado")
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Token não configurado!")
