# -*- coding: utf-8 -*-
import discord
from discord import app_commands
import aiohttp
from flask import Flask, request, jsonify
import threading
import asyncio
import os
import sys
import time
import json
from datetime import datetime
import pyotp

print("🚀 Iniciando G7 STORE FINAL (PRO EDITION)...")

# ===============================
# CONFIGURAÇÕES TÉCNICAS
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
INFINITE_TAG = "guilherme_vinicius90"
PORT = int(os.environ.get("PORT", 10000))

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    WEBHOOK_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"

# IDs DE CANAIS (FIXOS CONFORME SOLICITADO)
CANAL_CARRINHOS_ID = 1513770446158303304
CANAL_PAGOS_ID = 1513770547933089852
MEU_ID = 1431125477069688953

# ARQUIVOS DE DADOS
ARQUIVO_PRODUTOS = "produtos.json"
ARQUIVO_ESTOQUE = "estoque.json"
ARQUIVO_PAGAMENTOS = "pagamentos.json"

# LOCKS PARA SEGURANÇA DE DADOS
estoque_lock = threading.Lock()
pagamentos_lock = threading.Lock()

# ===============================
# GESTÃO DE DADOS (JSON)
# ===============================
def carregar_json(arq, default):
    if os.path.exists(arq):
        try:
            with open(arq, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default
    return default

def salvar_json_sync(arq, dados):
    try:
        with open(arq, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    except Exception as e: print(f"❌ Erro ao salvar {arq}: {e}")

produtos_disponiveis = carregar_json(ARQUIVO_PRODUTOS, {})
estoque_disponivel = carregar_json(ARQUIVO_ESTOQUE, {})
pagamentos_processados = set(carregar_json(ARQUIVO_PAGAMENTOS, []))

def salvar_tudo_sync():
    salvar_json_sync(ARQUIVO_PRODUTOS, produtos_disponiveis)
    salvar_json_sync(ARQUIVO_ESTOQUE, estoque_disponivel)
    salvar_json_sync(ARQUIVO_PAGAMENTOS, list(pagamentos_processados))

# ===============================
# SISTEMA DE ESTOQUE E ENTREGA
# ===============================
def realizar_baixa_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return None
        
        # Se for variação
        if variacao_nome and variacao_nome != "NONE":
            vars_estoque = estoque_disponivel[produto_id].get("variacoes", {})
            itens = vars_estoque.get(variacao_nome, [])
            if itens:
                item = itens.pop(0)
                salvar_tudo_sync()
                return item
            return None
        
        # Se for produto simples
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens:
            item = itens.pop(0)
            salvar_tudo_sync()
            return item
        return None

def consultar_qtd_estoque(produto_id, variacao_nome=None):
    if produto_id not in estoque_disponivel: return 0
    if variacao_nome:
        return len(estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, []))
    
    # Soma total (simples + variações)
    total = len(estoque_disponivel[produto_id].get("itens", []))
    vars_dict = estoque_disponivel[produto_id].get("variacoes", {})
    for v in vars_dict.values(): total += len(v)
    return total

# ===============================
# LOGS E NOTIFICAÇÕES
# ===============================
carrinhos_ativos = {} # pay_id -> msg_id

async def notify_pagamento(user, prod_nome, valor, pay_id, item=None):
    # 1. LOG NO CANAL DE PAGAMENTOS CONFIRMADOS (ID: 1513770547933089852)
    canal_pagos = bot.get_channel(CANAL_PAGOS_ID)
    if canal_pagos:
        embed = discord.Embed(title="✅ NOVO PAGAMENTO CONFIRMADO", color=0x00ff88, timestamp=datetime.now())
        embed.add_field(name="👤 Cliente", value=f"{user.mention} ({user.id})", inline=False)
        embed.add_field(name="📦 Produto", value=prod_nome, inline=True)
        embed.add_field(name="💰 Valor", value=f"R$ {valor:.2f}", inline=True)
        if item:
            embed.add_field(name="🔐 Item Entregue", value=f"```{item}```", inline=False)
        else:
            embed.add_field(name="⚠️ Status", value="```Estoque esgotado - Entrega Manual Necessária```", inline=False)
        embed.set_footer(text=f"ID: {pay_id}")
        await canal_pagos.send(embed=embed)

    # 2. ATUALIZAR CANAL DE CARRINHOS (ID: 1513770446158303304)
    if str(pay_id) in carrinhos_ativos:
        canal_carrinhos = bot.get_channel(CANAL_CARRINHOS_ID)
        if canal_carrinhos:
            try:
                msg = await canal_carrinhos.fetch_message(carrinhos_ativos[str(pay_id)])
                new_embed = discord.Embed(title="✅ CARRINHO FINALIZADO", description=f"O cliente {user.mention} concluiu o pagamento.", color=0x00ff88)
                new_embed.add_field(name="Produto", value=prod_nome)
                await msg.edit(embed=new_embed)
            except: pass
        del carrinhos_ativos[str(pay_id)]

# ===============================
# BOT CORE
# ===============================
class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)
    async def setup_hook(self):
        await self.tree.sync()
    async def on_ready(self):
        print(f"🟢 Bot Online: {self.user}")

bot = MyBot()

# ===============================
# COMANDOS
# ===============================
@bot.tree.command(name="criar_produto", description="[ADMIN] Criar novo produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "auto"):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "tipo": tipo, "variacoes": []}
    if id not in estoque_disponivel: estoque_disponivel[id] = {"itens": [], "variacoes": {}}
    salvar_tudo_sync()
    await interaction.response.send_message(f"✅ Produto `{id}` criado!", ephemeral=True)

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    if produto_id not in estoque_disponivel: estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
    
    if variacao:
        if "variacoes" not in estoque_disponivel[produto_id]: estoque_disponivel[produto_id]["variacoes"] = {}
        if variacao not in estoque_disponivel[produto_id]["variacoes"]: estoque_disponivel[produto_id]["variacoes"][variacao] = []
        estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos)
    else:
        estoque_disponivel[produto_id]["itens"].extend(novos)
    
    salvar_tudo_sync()
    await interaction.response.send_message(f"✅ {len(novos)} itens adicionados ao estoque de `{produto_id}`", ephemeral=True)

@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    if produto_id not in produtos_disponiveis: return await interaction.response.send_message("❌ Produto não existe", ephemeral=True)
    
    if "variacoes" not in produtos_disponiveis[produto_id]: produtos_disponiveis[produto_id]["variacoes"] = []
    produtos_disponiveis[produto_id]["variacoes"].append({"nome": nome, "preco": preco})
    salvar_tudo_sync()
    await interaction.response.send_message(f"✅ Variação `{nome}` adicionada ao produto `{produto_id}`", ephemeral=True)

@bot.tree.command(name="sincronizar", description="[ADMIN] Enviar painel de vendas")
async def sincronizar(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    p = produtos_disponiveis.get(produto_id)
    if not p: return await interaction.response.send_message("❌ Produto não encontrado", ephemeral=True)
    
    qtd = consultar_qtd_estoque(produto_id)
    embed = discord.Embed(title=f"🛒 {p['nome']}", description=f"{p['descricao']}\n\n📦 Estoque: **{qtd}**", color=0x5865F2)
    embed.add_field(name="💰 Preço", value=f"R$ {p['preco']:.2f}")
    
    view = discord.ui.View(timeout=None)
    btn = discord.ui.Button(label="Comprar", style=discord.ButtonStyle.success, custom_id=f"buy_{produto_id}")
    
    async def buy_callback(inter: discord.Interaction):
        p_info = produtos_disponiveis.get(produto_id)
        if p_info.get("variacoes"):
            # Menu de Variações
            select = discord.ui.Select(placeholder="Escolha uma variação...")
            for i, v in enumerate(p_info["variacoes"]):
                select.add_option(label=v["nome"], value=str(i), description=f"R$ {v['preco']:.2f}")
            
            async def select_callback(s_inter: discord.Interaction):
                var = p_info["variacoes"][int(select.values[0])]
                await gerar_link_e_notificar(s_inter, produto_id, var["preco"], f"{p_info['nome']} ({var['nome']})", var["nome"])
            
            select.callback = select_callback
            v_view = discord.ui.View(); v_view.add_item(select)
            await inter.response.send_message("Selecione a opção desejada:", view=v_view, ephemeral=True)
        else:
            await gerar_link_e_notificar(inter, produto_id, p_info["preco"], p_info["nome"])

    btn.callback = buy_callback
    view.add_item(btn)
    await canal.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Painel enviado!", ephemeral=True)

async def gerar_link_e_notificar(inter, p_id, preco, nome_completo, var_nome="NONE"):
    await inter.response.defer(ephemeral=True)
    
    # Criar NSU Seguro
    nsu = f"{p_id}|{inter.user.id}|{var_nome}|{int(time.time())}"
    payload = {
        "handle": INFINITE_TAG,
        "order_nsu": nsu,
        "items": [{"quantity": 1, "price": int(preco * 100), "description": nome_completo[:60]}]
    }
    if WEBHOOK_URL: payload["webhook_url"] = WEBHOOK_URL
    
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.checkout.infinitepay.io/links", json=payload) as resp:
            if resp.status in [200, 201]:
                data = await resp.json()
                pay_url = data.get("url")
                pay_id = data.get("invoice_slug")
                
                # Log Carrinho Ativo
                canal_c = bot.get_channel(CANAL_CARRINHOS_ID)
                if canal_c:
                    emb = discord.Embed(title="🛒 NOVO CARRINHO", color=0xffaa00)
                    emb.add_field(name="Cliente", value=inter.user.mention)
                    emb.add_field(name="Produto", value=nome_completo)
                    emb.add_field(name="Valor", value=f"R$ {preco:.2f}")
                    msg = await canal_c.send(embed=emb)
                    carrinhos_ativos[str(pay_id)] = msg.id
                
                # Enviar para o Cliente
                btn_pay = discord.ui.View(); btn_pay.add_item(discord.ui.Button(label="Pagar Agora", url=pay_url))
                await inter.user.send(f"✅ Seu link de pagamento para **{nome_completo}** foi gerado!", view=btn_pay)
                await inter.followup.send("📨 Verifique seu privado (DM) para o link de pagamento!", ephemeral=True)
            else:
                await inter.followup.send("❌ Erro ao gerar link. Tente novamente.", ephemeral=True)

# ===============================
# WEBHOOK (FLASK)
# ===============================
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def infinite_webhook():
    data = request.json
    if not data or data.get("status") != "paid": return "OK", 200
    
    slug = data.get("invoice_slug")
    with pagamentos_lock:
        if slug in pagamentos_processados: return "OK", 200
        pagamentos_processados.add(slug)
        salvar_tudo_sync()

    # Processar NSU: p_id|u_id|var|time
    nsu = data.get("order_nsu", "")
    parts = nsu.split('|')
    if len(parts) < 3: return "OK", 200
    
    p_id, u_id, v_name = parts[0], int(parts[1]), parts[2]
    valor = float(data.get("amount", 0)) / 100
    
    # AGENDAR ENTREGA NO DISCORD (THREAD-SAFE)
    asyncio.run_coroutine_threadsafe(executar_entrega(p_id, u_id, v_name, valor, slug), bot.loop)
    return "OK", 200

async def executar_entrega(p_id, u_id, v_name, valor, slug):
    try:
        user = await bot.fetch_user(u_id)
        p_info = produtos_disponiveis.get(p_id, {"nome": p_id})
        nome_exibicao = f"{p_info['nome']} ({v_name})" if v_name != "NONE" else p_info['nome']
        
        # Baixa no estoque
        item = realizar_baixa_estoque(p_id, v_name)
        
        # 1. Notificar Canais (O MAIS IMPORTANTE)
        await notify_pagamento(user, nome_exibicao, valor, slug, item)
        
        # 2. Entregar ao Cliente
        if item:
            emb = discord.Embed(title="🎁 ENTREGA REALIZADA!", color=0x00ff88)
            emb.add_field(name="Produto", value=nome_exibicao, inline=False)
            emb.add_field(name="Seu Código/Item", value=f"```{item}```", inline=False)
            emb.set_footer(text="Obrigado pela preferência!")
            await user.send(embed=emb)
        else:
            await user.send(f"✅ **Pagamento Confirmado!**\n📦 Produto: **{nome_exibicao}**\n⚠️ Infelizmente o estoque acabou no último segundo. O administrador foi notificado e fará sua entrega manual em breve!")
            
    except Exception as e:
        print(f"❌ Erro fatal na entrega: {e}")

# ===============================
# INICIALIZAÇÃO
# ===============================
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("❌ Token não configurado!")
