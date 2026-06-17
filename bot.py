import discord
from discord import app_commands
import requests
from flask import Flask, request
import threading
import asyncio
import os
import sys
import time
import json
from datetime import datetime
import pyotp
import gc
import base64
from io import BytesIO

print("🚀 Iniciando G7 STORE ULTRA (Integrated & Advanced Edition)...")

# ===============================
# CONFIGURAÇÕES
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
INFINITE_TAG = "guilherme_vinicius90"

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    WEBHOOK_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"

# IDs ORIGINAIS
GUILD_ID = 1472114509068898367
CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
MEU_ID = 1431125477069688953

# ARQUIVOS
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

# ===============================
# PERSISTÊNCIA E LOCKS
# ===============================
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()

def inicializar_arquivos():
    for arq, default in {ARQUIVO_PRODUTOS_JSON: {}, ARQUIVO_ESTOQUE_JSON: {}, ARQUIVO_PAGAMENTOS_PROCESSADOS: []}.items():
        if not os.path.exists(arq):
            with open(arq, 'w', encoding='utf-8') as f: json.dump(default, f, indent=2, ensure_ascii=False)

inicializar_arquivos()

def carregar_json(caminho):
    try:
        with open(caminho, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def salvar_json(caminho, dados):
    try:
        with open(caminho, 'w', encoding='utf-8') as f: json.dump(dados, f, indent=2, ensure_ascii=False)
    except Exception as e: print(f"❌ Erro ao salvar {caminho}: {e}")

produtos_disponiveis = carregar_json(ARQUIVO_PRODUTOS_JSON)
estoque_disponivel = carregar_json(ARQUIVO_ESTOQUE_JSON)
pagamentos_processados = set(carregar_json(ARQUIVO_PAGAMENTOS_PROCESSADOS))

def salvar_tudo():
    salvar_json(ARQUIVO_PRODUTOS_JSON, produtos_disponiveis)
    salvar_json(ARQUIVO_ESTOQUE_JSON, estoque_disponivel)
    salvar_json(ARQUIVO_PAGAMENTOS_PROCESSADOS, list(pagamentos_processados))

# ===============================
# GATEWAY INFINITE PAY
# ===============================
def criar_pagamento_infinite(user_id, produto_id, preco, nome_produto):
    try:
        valor_float = float(preco)
        if valor_float < 1.0: return {"erro": "Valor mínimo R$ 1,00."}
        preco_centavos = int(round(valor_float * 100))
        order_nsu = f"{produto_id}_{user_id}_{int(time.time())}"
        payload = {
            "handle": INFINITE_TAG,
            "order_nsu": order_nsu,
            "items": [{"quantity": 1, "price": preco_centavos, "description": f"Compra: {nome_produto}"[:60]}]
        }
        if WEBHOOK_URL and WEBHOOK_URL.startswith("https"): payload["webhook_url"] = WEBHOOK_URL
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        response = requests.post("https://api.checkout.infinitepay.io/links", json=payload, headers=headers, timeout=15)
        if response.status_code in [200, 201]:
            data = response.json()
            return {"payment_url": data.get("url"), "produto": nome_produto, "preco": valor_float, "payment_id": data.get("invoice_slug"), "produto_id": produto_id}
        return {"erro": f"InfinitePay {response.status_code}"}
    except Exception as e: return {"erro": str(e)}

# ===============================
# SISTEMA DE ESTOQUE
# ===============================
def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return None
        if variacao_nome:
            itens = estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, [])
            if itens:
                item = itens.pop(0)
                salvar_tudo()
                return item
            return None
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens:
            item = itens.pop(0)
            salvar_tudo()
            return item
        return None

def verificar_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel: return 0
        if variacao_nome: return len(estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, []))
        p_info = produtos_disponiveis.get(produto_id, {})
        if p_info.get("variacoes"):
            return sum(len(estoque_disponivel[produto_id].get("variacoes", {}).get(v["nome"], [])) for v in p_info["variacoes"])
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# DESIGN TZADA
# ===============================
async def criar_embed_produto(produto_id, p_info):
    qtd = verificar_estoque(produto_id)
    tipo = "🤖 Entrega Automática!" if p_info.get('tipo') == 'auto' else "👨‍💼 Entrega Manual"
    desc = p_info.get('descricao', 'Sem descrição').replace('|', '\n✅ ')
    embed = discord.Embed(title=f"⚡ {tipo}", description=f"**{p_info['nome']}**\n\n✅ {desc}\n\n📦 Estoque: {qtd}", color=0xffa500)
    if p_info.get('imagem'): embed.set_image(url=p_info['imagem'])
    embed.add_field(name="💰 Valor", value=f"R$ {p_info['preco']:.2f}", inline=True)
    embed.set_footer(text="G7 STORE - Qualidade e Rapidez")
    return embed

# ===============================
# VIEWS
# ===============================
class VariacoesView(discord.ui.View):
    def __init__(self, produto_id, produto_nome, variacoes):
        super().__init__(timeout=300)
        options = [discord.SelectOption(label=v["nome"], description=f"R$ {v['preco']:.2f}", value=str(i)) for i, v in enumerate(variacoes)]
        select = discord.ui.Select(placeholder="Escolha uma opção...", options=options)
        select.callback = self.callback
        self.add_item(select)
        self.produto_id, self.produto_nome, self.variacoes = produto_id, produto_nome, variacoes
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        v = self.variacoes[int(interaction.data["values"][0])]
        res = criar_pagamento_infinite(interaction.user.id, f"{self.produto_id}_{v['nome']}", v["preco"], f"{self.produto_nome} - {v['nome']}")
        if "erro" in res: await interaction.followup.send(f"❌ Erro: {res['erro']}", ephemeral=True)
        else:
            view = discord.ui.View(); view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=res['payment_url']))
            await interaction.user.send(embed=discord.Embed(title="🧾 PAGAMENTO", description=f"**Produto:** {res['produto']}\n**Valor:** R$ {res['preco']:.2f}", color=0x00ff88), view=view)
            await interaction.followup.send("📨 Link enviado no privado!", ephemeral=True)
            await log_carrinho(interaction.user, res['produto'], res['preco'], res['payment_id'])

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id, p_info):
        super().__init__(timeout=None)
        self.produto_id, self.p_info = produto_id, p_info
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_buy")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if self.p_info.get("variacoes"):
            await interaction.followup.send("Escolha a opção:", view=VariacoesView(self.produto_id, self.p_info["nome"], self.p_info["variacoes"]), ephemeral=True)
        else:
            res = criar_pagamento_infinite(interaction.user.id, self.produto_id, self.p_info["preco"], self.p_info["nome"])
            if "erro" in res: await interaction.followup.send(f"❌ Erro: {res['erro']}", ephemeral=True)
            else:
                view = discord.ui.View(); view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=res['payment_url']))
                await interaction.user.send(embed=discord.Embed(title="🧾 PAGAMENTO", description=f"**Produto:** {res['produto']}\n**Valor:** R$ {res['preco']:.2f}", color=0x00ff88), view=view)
                await interaction.followup.send("📨 Link enviado no privado!", ephemeral=True)
                await log_carrinho(interaction.user, res['produto'], res['preco'], res['payment_id'])

# ===============================
# LOGS
# ===============================
carrinhos_ativos = {}
async def log_carrinho(user, prod, valor, pay_id):
    canal = bot.get_channel(CANAL_CARRINHOS)
    if canal:
        embed = discord.Embed(title="🛒 NOVO CARRINHO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention).add_field(name="Produto", value=prod).add_field(name="Valor", value=f"R$ {valor:.2f}")
        msg = await canal.send(embed=embed)
        carrinhos_ativos[str(pay_id)] = {"msg_id": msg.id, "user_id": user.id, "prod": prod}

async def log_sucesso(user, prod, valor, pay_id, item=None):
    canal = bot.get_channel(CANAL_PAGOS)
    if canal:
        embed = discord.Embed(title="✅ PAGAMENTO CONFIRMADO", color=0x00ff88, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention).add_field(name="Produto", value=prod).add_field(name="Valor", value=f"R$ {valor:.2f}")
        if item: embed.add_field(name="🔐 Item", value=f"```{item}```", inline=False)
        await canal.send(embed=embed)
    if str(pay_id) in carrinhos_ativos:
        try:
            c_canal = bot.get_channel(CANAL_CARRINHOS)
            msg = await c_canal.fetch_message(carrinhos_ativos[str(pay_id)]["msg_id"])
            await msg.edit(embed=discord.Embed(title="✅ APROVADO", description=f"Cliente: {user.mention}\nProduto: {prod}", color=0x00ff88))
        except: pass
        del carrinhos_ativos[str(pay_id)]

# ===============================
# BOT CORE
# ===============================
class Bot(discord.Client):
    def __init__(self): super().__init__(intents=discord.Intents.all()); self.tree = app_commands.CommandTree(self)
    async def setup_hook(self): await self.tree.sync()
    async def on_ready(self): print(f"🟢 Logado como {self.user}")

bot = Bot()

# ===============================
# COMANDOS ADMIN - GESTÃO TOTAL
# ===============================

# 1. /criar_produto
@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "auto"):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    produtos_disponiveis[id] = {"nome": nome, "preco": preco, "descricao": descricao, "tipo": tipo, "imagem": "", "variacoes": []}
    with estoque_lock: estoque_disponivel[id] = {"itens": [], "variacoes": {}}
    salvar_tudo(); await interaction.response.send_message(f"✅ Produto `{id}` criado!", ephemeral=True)

# 2. /editar_produto (NOME, DESC, TIPO, IMAGEM, PREÇO)
@bot.tree.command(name="editar_produto", description="[ADMIN] Editar campos de um produto")
@app_commands.describe(campo="O que deseja editar", valor="Novo valor")
@app_commands.choices(campo=[
    app_commands.Choice(name="Nome", value="nome"),
    app_commands.Choice(name="Descrição", value="descricao"),
    app_commands.Choice(name="Preço", value="preco"),
    app_commands.Choice(name="Tipo (auto/manual)", value="tipo"),
    app_commands.Choice(name="Imagem (URL)", value="imagem")
])
async def editar_produto(interaction: discord.Interaction, produto_id: str, campo: str, valor: str):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    if produto_id not in produtos_disponiveis: return await interaction.response.send_message("❌ Não encontrado", ephemeral=True)
    
    if campo == "preco": produtos_disponiveis[produto_id][campo] = float(valor)
    else: produtos_disponiveis[produto_id][campo] = valor
    salvar_tudo(); await interaction.response.send_message(f"✅ {campo} de `{produto_id}` atualizado!", ephemeral=True)

# 3. /add_variacao
@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    if produto_id not in produtos_disponiveis: return await interaction.response.send_message("❌ Não encontrado", ephemeral=True)
    produtos_disponiveis[produto_id].setdefault("variacoes", []).append({"nome": nome, "preco": preco})
    salvar_tudo(); await interaction.response.send_message(f"✅ Variação `{nome}` adicionada!", ephemeral=True)

# 4. /editar_variacao
@bot.tree.command(name="editar_variacao", description="[ADMIN] Editar uma variação")
async def editar_variacao(interaction: discord.Interaction, produto_id: str, indice: int, novo_nome: str = None, novo_preco: float = None):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    variacoes = produtos_disponiveis.get(produto_id, {}).get("variacoes", [])
    if 0 <= indice < len(variacoes):
        if novo_nome: variacoes[indice]["nome"] = novo_nome
        if novo_preco: variacoes[indice]["preco"] = novo_preco
        salvar_tudo(); await interaction.response.send_message("✅ Variação editada!", ephemeral=True)
    else: await interaction.response.send_message("❌ Índice inválido", ephemeral=True)

# 5. /remover_variacao
@bot.tree.command(name="remover_variacao", description="[ADMIN] Remover uma variação")
async def remover_variacao(interaction: discord.Interaction, produto_id: str, indice: int):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    variacoes = produtos_disponiveis.get(produto_id, {}).get("variacoes", [])
    if 0 <= indice < len(variacoes):
        removida = variacoes.pop(indice)
        salvar_tudo(); await interaction.response.send_message(f"✅ Variação `{removida['nome']}` removida!", ephemeral=True)
    else: await interaction.response.send_message("❌ Índice inválido", ephemeral=True)

# 6. /add_estoque
@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens (separar por |)")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    with estoque_lock:
        est = estoque_disponivel.setdefault(produto_id, {"itens": [], "variacoes": {}})
        if variacao: est.setdefault("variacoes", {}).setdefault(variacao, []).extend(novos)
        else: est.setdefault("itens", []).extend(novos)
    salvar_tudo(); await interaction.response.send_message(f"✅ {len(novos)} itens adicionados!", ephemeral=True)

# 7. /ver_estoque (MOSTRA ÍNDICES)
@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver itens e seus índices")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    est = estoque_disponivel.get(produto_id, {})
    itens = est.get("variacoes", {}).get(variacao, []) if variacao else est.get("itens", [])
    if not itens: return await interaction.response.send_message("📦 Estoque vazio", ephemeral=True)
    txt = "\n".join([f"**{i}**: `{item}`" for i, item in enumerate(itens[:30])])
    await interaction.response.send_message(f"📦 **Estoque {produto_id}**\n{txt}", ephemeral=True)

# 8. /remover_estoque_indice
@bot.tree.command(name="remover_estoque_indice", description="[ADMIN] Remover item específico pelo índice")
async def remover_estoque_indice(interaction: discord.Interaction, produto_id: str, indice: int, variacao: str = None):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    with estoque_lock:
        est = estoque_disponivel.get(produto_id, {})
        lista = est.get("variacoes", {}).get(variacao, []) if variacao else est.get("itens", [])
        if 0 <= indice < len(lista):
            removido = lista.pop(indice)
            salvar_tudo(); await interaction.response.send_message(f"✅ Item `{removido}` removido!", ephemeral=True)
        else: await interaction.response.send_message("❌ Índice inválido", ephemeral=True)

# 9. /listar_produtos
@bot.tree.command(name="listar_produtos", description="[ADMIN] Listar todos os IDs e nomes")
async def listar_produtos(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    txt = "\n".join([f"🆔 `{pid}` - **{p['nome']}** (R$ {p['preco']:.2f})" for pid, p in produtos_disponiveis.items()])
    await interaction.response.send_message(f"📋 **PRODUTOS:**\n{txt or 'Nenhum'}", ephemeral=True)

# 10. /sincronizar_canal
@bot.tree.command(name="sincronizar_canal", description="[ADMIN] Enviar embed de venda")
async def sincronizar_canal(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    p = produtos_disponiveis.get(produto_id)
    if not p: return await interaction.response.send_message("❌ Não encontrado", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    async for m in canal.history(limit=20): 
        if m.author == bot.user: await m.delete()
    await canal.send(embed=await criar_embed_produto(produto_id, p), view=ProdutoCompraView(produto_id, p))
    await interaction.followup.send("✅ Canal sincronizado!", ephemeral=True)

# 11. /configurar_2fa
@bot.tree.command(name="configurar_2fa", description="[ADMIN] Canal de 2FA")
async def configurar_2fa(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    class V(discord.ui.View):
        @discord.ui.button(label="🔐 Gerar 2FA", style=discord.ButtonStyle.primary)
        async def b(self, i, bt):
            class M(discord.ui.Modal, title="2FA"):
                c = discord.ui.TextInput(label="Chave")
                async def on_submit(self, i): await i.response.send_message(f"🔐 Código: **{pyotp.TOTP(self.c.value.strip().upper()).now()}**", ephemeral=True)
            await i.response.send_modal(M())
    await interaction.channel.send(embed=discord.Embed(title="🔐 GERADOR 2FA", description="Clique para gerar"), view=V())
    await interaction.response.send_message("✅ OK", ephemeral=True)

# 12. /limpar
@bot.tree.command(name="limpar", description="[ADMIN] Limpar chat")
async def limpar(interaction: discord.Interaction, qtd: int = 100):
    if interaction.user.id != MEU_ID: return await interaction.response.send_message("❌ Sem permissão", ephemeral=True)
    await interaction.response.defer(ephemeral=True); await interaction.channel.purge(limit=qtd)
    await interaction.followup.send(f"✅ {qtd} limpas", ephemeral=True)

# ===============================
# WEBHOOK SERVER
# ===============================
app = Flask(__name__)
@app.route('/webhook', methods=['POST'])
def webhook():
    d = request.json
    if d.get("status") == "paid" and d.get("order_nsu"):
        with webhook_lock:
            slug = d.get("invoice_slug")
            if slug in pagamentos_processados: return "OK", 200
            pagamentos_processados.add(slug); salvar_tudo()
            pts = d["order_nsu"].split('_')
            if len(pts) >= 3:
                u_id = int(pts[-2]); p_ref = pts[0]; p_id = None; v_n = None
                if p_ref in produtos_disponiveis: p_id = p_ref
                else:
                    for pid in produtos_disponiveis:
                        if pid in p_ref: p_id = pid; v_n = p_ref.replace(pid, "").replace("_", ""); break
                if p_id:
                    p_info = produtos_disponiveis[p_id]
                    if p_info.get("tipo") == "auto":
                        it = entregar_do_estoque(p_id, v_n)
                        async def deliver():
                            u = await bot.fetch_user(u_id)
                            try:
                                if it: await u.send(f"✅ **Aprovado!**\n📦 **{p_info['nome']}**\n🔐 **Item:**\n```{it}```")
                                else: await u.send(f"✅ **Aprovado!**\n📦 **{p_info['nome']}**\n⚠️ Estoque esgotado, admin entregará.")
                                await log_sucesso(u, p_info['nome'], float(d.get('amount', 0))/100, slug, it)
                            except: pass
                        asyncio.run_coroutine_threadsafe(deliver(), bot.loop)
    return "OK", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000), daemon=True).start()
    if DISCORD_TOKEN: bot.run(DISCORD_TOKEN)
    else: print("⚠️ Sem Token")
