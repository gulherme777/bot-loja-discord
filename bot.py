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

print("🔧 Iniciando bot da G7 STORE...")

# ===============================
# CONFIGURAÇÕES
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
INFINITE_TAG = "guilherme_vinicius90"

if not DISCORD_TOKEN:
    print("❌ ERRO: DISCORD_TOKEN não encontrado!")
    sys.exit(1)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    WEBHOOK_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"

# ===============================
# ARQUIVOS
# ===============================
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

def inicializar_arquivos():
    arquivos_padrao = {
        ARQUIVO_PRODUTOS_JSON: {},
        ARQUIVO_ESTOQUE_JSON: {},
        ARQUIVO_PAGAMENTOS_PROCESSADOS: []
    }
    for arquivo, conteudo in arquivos_padrao.items():
        if not os.path.exists(arquivo):
            with open(arquivo, 'w', encoding='utf-8') as f:
                json.dump(conteudo, f, indent=2, ensure_ascii=False)
            print(f"✅ Arquivo {arquivo} criado")

inicializar_arquivos()

# ===============================
# IDs
# ===============================
GUILD_ID = 1472114509068898367
CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
MEU_ID = 1431125477069688953

carrinhos_ativos = {}
mensagens_canais = {}

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
        except:
            return default if default is not None else {}
    return default if default is not None else {}

def salvar_json(caminho, dados):
    try:
        with open(caminho, 'w', encoding='utf-8') as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    except:
        pass

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
            return {"erro": "Valor mínimo R$ 1,00."}
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
            "User-Agent": "Mozilla/5.0"
        }
        response = requests.post("https://api.checkout.infinitepay.io/links", json=payload, headers=headers, timeout=15)
        if response.status_code in [200, 201]:
            data = response.json()
            return {
                "payment_url": data.get("url"),
                "produto": nome_produto,
                "preco": float(preco),
                "payment_id": data.get("invoice_slug"),
                "produto_id": produto_id
            }
        return {"erro": f"InfinitePay {response.status_code}"}
    except Exception as e:
        return {"erro": str(e)}

def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return None
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
        if produto_id not in estoque_disponivel:
            return 0
        if variacao_nome:
            return len(estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, []))
        produto_info = produtos_disponiveis.get(produto_id, {})
        variacoes = produto_info.get("variacoes", [])
        if variacoes:
            return sum(len(estoque_disponivel[produto_id].get("variacoes", {}).get(v["nome"], [])) for v in variacoes)
        return len(estoque_disponivel[produto_id].get("itens", []))

def remover_itens_estoque(produto_id, itens_para_remover, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return False, "Produto não encontrado no estoque"
        removidos = []
        nao_encontrados = []
        if variacao_nome:
            if variacao_nome not in estoque_disponivel[produto_id].get("variacoes", {}):
                return False, f"Variação '{variacao_nome}' não encontrada"
            estoque_atual = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
            for item in itens_para_remover:
                if item in estoque_atual:
                    estoque_atual.remove(item)
                    removidos.append(item)
                else:
                    nao_encontrados.append(item)
        else:
            estoque_atual = estoque_disponivel[produto_id].get("itens", [])
            for item in itens_para_remover:
                if item in estoque_atual:
                    estoque_atual.remove(item)
                    removidos.append(item)
                else:
                    nao_encontrados.append(item)
        salvar_estoque(estoque_disponivel)
        if removidos and not nao_encontrados:
            return True, f"✅ {len(removidos)} itens removidos!"
        elif removidos and nao_encontrados:
            return True, f"⚠️ {len(removidos)} removidos, {len(nao_encontrados)} não encontrados"
        else:
            return False, "❌ Nenhum item encontrado"

async def criar_embed_produto_tzada(produto_id, p_info):
    qtd = verificar_estoque(produto_id)
    tipo = "🤖 Entrega Automática!" if p_info.get('tipo') == 'auto' else "👨‍💼 Entrega Manual"
    desc = p_info.get('descricao', '').replace('|', '\n✅ ')
    embed = discord.Embed(
        title=f"⚡ {tipo}",
        description=f"**{p_info['nome']}**\n\n✅ {desc}\n\n📦 Estoque: {qtd}",
        color=0xffa500
    )
    if p_info.get('imagem'):
        embed.set_image(url=p_info['imagem'])
    embed.add_field(name="💰 Valor", value=f"R$ {p_info['preco']:.2f}", inline=True)
    return embed

# ===============================
# LOGS
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal:
            return None
        embed = discord.Embed(title="🛒 NOVO CARRINHO ATIVO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {
            "canal": canal.id,
            "mensagem_id": mensagem.id,
            "usuario": user.id,
            "produto": produto_nome
        }
        return mensagem
    except:
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
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    embed_aprovado = discord.Embed(
                        title="✅ PAGAMENTO APROVADO",
                        description=f"Cliente: {user.mention}\nProduto: {produto_nome}\nValor: R$ {valor:.2f}",
                        color=0x00ff88
                    )
                    await msg.edit(embed=embed_aprovado)
                except:
                    pass
            del carrinhos_ativos[str(pagamento_id)]
    except:
        pass

# ===============================
# VIEWS E MODAIS
# ===============================
class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
    chave = discord.ui.TextInput(label="Chave 2FA", placeholder="Cole sua chave aqui...", min_length=16, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            totp = pyotp.TOTP(self.chave.value.strip().upper())
            codigo = totp.now()
            embed = discord.Embed(title="🔐 CÓDIGO 2FA", description=f"```{codigo}```", color=0x00ff88)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

class VariacoesView(discord.ui.View):
    def __init__(self, produto_id, produto_nome, variacoes):
        super().__init__(timeout=300)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes
        options = [discord.SelectOption(label=v["nome"], description=f"R$ {v['preco']:.2f}", value=str(i)) for i, v in enumerate(variacoes)]
        select = discord.ui.Select(placeholder="Escolha uma opção...", options=options, custom_id="select_variacao")
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            indice = int(interaction.data["values"][0])
            v = self.variacoes[indice]
            pix_data = criar_pagamento_pix_com_preco(
                interaction.user.id,
                f"{self.produto_id}_{v['nome']}",
                v["preco"],
                f"{self.produto_nome} - {v['nome']}"
            )
            if "erro" in pix_data:
                await interaction.followup.send(f"❌ Erro: {pix_data['erro']}", ephemeral=True)
                return
            embed = discord.Embed(
                title="🧾 PAGAMENTO - G7 STORE",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para pagar.",
                color=0x00ff88
            )
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=pix_data['payment_url']))
            await interaction.user.send(embed=embed, view=view)
            await interaction.followup.send("📨 Link enviado no privado!", ephemeral=True)
            asyncio.create_task(log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data['payment_id']))
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id, produto_nome, variacoes=None):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes or []

    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_comprar")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            if self.variacoes:
                await interaction.followup.send("Selecione a opção:", view=VariacoesView(self.produto_id, self.produto_nome, self.variacoes), ephemeral=True)
                return
            p_info = produtos_disponiveis.get(self.produto_id)
            if not p_info:
                await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
                return
            pix_data = criar_pagamento_pix_com_preco(
                interaction.user.id,
                self.produto_id,
                p_info["preco"],
                self.produto_nome
            )
            if "erro" in pix_data:
                await interaction.followup.send(f"❌ Erro: {pix_data['erro']}", ephemeral=True)
                return
            embed = discord.Embed(
                title="🧾 PAGAMENTO - G7 STORE",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para pagar.",
                color=0x00ff88
            )
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=pix_data['payment_url']))
            await interaction.user.send(embed=embed, view=view)
            await interaction.followup.send("📨 Link enviado no privado!", ephemeral=True)
            asyncio.create_task(log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data['payment_id']))
        except Exception as e:
            await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# ===============================
# BOT
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        try:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print("✅ Slash commands sincronizados")
        except Exception as e:
            print(f"❌ Erro ao sincronizar: {e}")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")
        await self.change_presence(activity=discord.Game(name="G7 STORE 💎"))

bot = Bot()

# ===============================
# TODOS OS COMANDOS SLASH
# ===============================

# 1. /2fa
@bot.tree.command(name="2fa", description="Gerar código 2FA a partir da chave")
async def twofa(interaction: discord.Interaction):
    modal = Modal2FA()
    await interaction.response.send_modal(modal)

# 2. /add_estoque
@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao_indice: int = -1):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        novos = [i.strip() for i in itens.split("|") if i.strip()]
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
            if variacao_indice >= 0:
                if produto_id not in produtos_disponiveis:
                    await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
                    return
                v_nome = produtos_disponiveis[produto_id]["variacoes"][variacao_indice]["nome"]
                if v_nome not in estoque_disponivel[produto_id]["variacoes"]:
                    estoque_disponivel[produto_id]["variacoes"][v_nome] = []
                estoque_disponivel[produto_id]["variacoes"][v_nome].extend(novos)
            else:
                estoque_disponivel[produto_id]["itens"].extend(novos)
            salvar_estoque(estoque_disponivel)
        await interaction.followup.send(f"✅ {len(novos)} itens adicionados!", ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 3. /add_variacao
@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        if "variacoes" not in produtos_disponiveis[produto_id]:
            produtos_disponiveis[produto_id]["variacoes"] = []
        for v in produtos_disponiveis[produto_id]["variacoes"]:
            if v["nome"].lower() == nome.lower():
                await interaction.followup.send("❌ Variação já existe!", ephemeral=True)
                return
        produtos_disponiveis[produto_id]["variacoes"].append({"nome": nome, "preco": preco})
        salvar_produtos(produtos_disponiveis)
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
            estoque_disponivel[produto_id]["variacoes"][nome] = []
            salvar_estoque(estoque_disponivel)
        await interaction.followup.send(f"✅ Variação '{nome}' adicionada! R$ {preco:.2f}", ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 4. /backup
@bot.tree.command(name="backup", description="[ADMIN] Fazer backup dos produtos")
async def backup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        backup_data = {
            "timestamp": datetime.now().isoformat(),
            "produtos": produtos_disponiveis,
            "estoque": estoque_disponivel
        }
        salvar_json("backup_produtos.json", backup_data)
        embed = discord.Embed(title="💾 BACKUP REALIZADO", description=f"Backup em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", color=0x00ff88)
        embed.add_field(name="Produtos", value=len(produtos_disponiveis), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 5. /configurar_2fa
@bot.tree.command(name="configurar_2fa", description="[ADMIN] Configurar canal de 2FA com botão")
async def configurar_2fa(interaction: discord.Interaction, canal: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        embed = discord.Embed(title="🔐 GERADOR DE CÓDIGO 2FA", description="Clique no botão abaixo para gerar um código 2FA", color=0x00ff88)
        class Button2FA(discord.ui.View):
            @discord.ui.button(label="🔐 Gerar Código 2FA", style=discord.ButtonStyle.primary)
            async def gerar_2fa(self, interaction: discord.Interaction, button: discord.ui.Button):
                modal = Modal2FA()
                await interaction.response.send_modal(modal)
        await canal.send(embed=embed, view=Button2FA())
        await interaction.followup.send(f"✅ Canal {canal.mention} configurado!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 6. /criar_produto
@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "manual"):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if id in produtos_disponiveis:
            await interaction.followup.send("❌ ID já existe!", ephemeral=True)
            return
        produtos_disponiveis[id] = {
            "nome": nome,
            "preco": preco,
            "descricao": descricao,
            "tipo": tipo,
            "imagem": "",
            "variacoes": []
        }
        salvar_produtos(produtos_disponiveis)
        with estoque_lock:
            estoque_disponivel[id] = {"itens": [], "variacoes": {}}
            salvar_estoque(estoque_disponivel)
        embed = discord.Embed(title="✅ PRODUTO CRIADO", description=f"**ID:** `{id}`\n**Nome:** {nome}\n**Preço:** R$ {preco:.2f}", color=0x00ff88)
        await interaction.followup.send(embed=embed, ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 7. /editar_preco
@bot.tree.command(name="editar_preco", description="[ADMIN] Alterar preço de um produto")
async def editar_preco(interaction: discord.Interaction, produto_id: str, novo_preco: float, variacao_nome: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        if variacao_nome:
            for v in produtos_disponiveis[produto_id].get("variacoes", []):
                if v["nome"].lower() == variacao_nome.lower():
                    v["preco"] = novo_preco
                    salvar_produtos(produtos_disponiveis)
                    await interaction.followup.send(f"✅ Preço da variação alterado para R$ {novo_preco:.2f}!", ephemeral=True)
                    return
            await interaction.followup.send(f"❌ Variação não encontrada!", ephemeral=True)
        else:
            produtos_disponiveis[produto_id]["preco"] = novo_preco
            salvar_produtos(produtos_disponiveis)
            await interaction.followup.send(f"✅ Preço alterado para R$ {novo_preco:.2f}!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 8. /editor_produto
@bot.tree.command(name="editor_produto", description="[ADMIN] Alterar nome/descrição de um produto")
async def editor_produto(interaction: discord.Interaction, produto_id: str, novo_nome: str = None, nova_descricao: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        if novo_nome:
            produtos_disponiveis[produto_id]["nome"] = novo_nome
        if nova_descricao:
            produtos_disponiveis[produto_id]["descricao"] = nova_descricao
        salvar_produtos(produtos_disponiveis)
        await interaction.followup.send(f"✅ Produto atualizado!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 9. /entregar
@bot.tree.command(name="entregar", description="[ADMIN] Entregar produto manual do estoque")
async def entregar(interaction: discord.Interaction, usuario: discord.User, produto_id: str, variacao_nome: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        item = entregar_do_estoque(produto_id, variacao_nome)
        if not item:
            await interaction.followup.send("❌ Estoque vazio!", ephemeral=True)
            return
        p_info = produtos_disponiveis[produto_id]
        embed = discord.Embed(title="📦 ENTREGA MANUAL", description=f"**Produto:** {p_info['nome']}\n**Usuário:** {usuario.mention}\n**Item:** `{item}`", color=0x00ff88)
        await interaction.followup.send(embed=embed, ephemeral=True)
        await usuario.send(f"✅ **Sua compra foi entregue!**\n\n📦 **{p_info['nome']}**\n\n🔐 **Produto:**\n```{item}```")
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 10. /limpar
@bot.tree.command(name="limpar", description="[ADMIN] Limpar mensagens do canal")
async def limpar(interaction: discord.Interaction, quantidade: int = 100):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if quantidade > 1000:
            quantidade = 1000
        deleted = await interaction.channel.purge(limit=quantidade)
        await interaction.followup.send(f"✅ {len(deleted)} mensagens deletadas!", ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 11. /listar_produtos
@bot.tree.command(name="listar_produtos", description="[ADMIN] Listar todos os produtos")
async def listar_produtos(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if not produtos_disponiveis:
            await interaction.followup.send("📭 Nenhum produto!", ephemeral=True)
            return
        embed = discord.Embed(title="📦 LISTA DE PRODUTOS", description=f"Total: {len(produtos_disponiveis)}", color=0x00ff88)
        for pid, p in list(produtos_disponiveis.items())[:20]:
            qtd = verificar_estoque(pid)
            embed.add_field(name=f"`{pid}` - {p['nome']}", value=f"💰 R$ {p['preco']:.2f} | 📦 {qtd} itens", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 12. /listar_variacoes
@bot.tree.command(name="listar_variacoes", description="[ADMIN] Listar variações de um produto")
async def listar_variacoes(interaction: discord.Interaction, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
        if not variacoes:
            await interaction.followup.send(f"📭 Sem variações!", ephemeral=True)
            return
        embed = discord.Embed(title=f"🔄 VARIAÇÕES - {produtos_disponiveis[produto_id]['nome']}", color=0x00ff88)
        for i, v in enumerate(variacoes):
            qtd = verificar_estoque(produto_id, v["nome"])
            embed.add_field(name=f"{i+1}. {v['nome']}", value=f"💰 R$ {v['preco']:.2f} | 📦 {qtd} itens", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 13. /remover_estoque
@bot.tree.command(name="remover_estoque", description="[ADMIN] Remove itens específicos do estoque")
async def remover_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao_nome: str = None):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        itens_para_remover = [i.strip() for i in itens.split("|") if i.strip()]
        if not itens_para_remover:
            await interaction.followup.send("❌ Nenhum item válido!", ephemeral=True)
            return
        sucesso, mensagem = remover_itens_estoque(produto_id, itens_para_remover, variacao_nome)
        await interaction.followup.send(mensagem, ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 14. /remover_produto
@bot.tree.command(name="remover_produto", description="[ADMIN] Remover um produto")
async def remover_produto(interaction: discord.Interaction, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        nome = produtos_disponiveis[produto_id]["nome"]
        del produtos_disponiveis[produto_id]
        salvar_produtos(produtos_disponiveis)
        with estoque_lock:
            if produto_id in estoque_disponivel:
                del estoque_disponivel[produto_id]
                salvar_estoque(estoque_disponivel)
        await interaction.followup.send(f"✅ Produto '{nome}' removido!", ephemeral=True)
        gc.collect()
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 15. /remover_variacao
@bot.tree.command(name="remover_variacao", description="[ADMIN] Remover uma variação de um produto")
async def remover_variacao(interaction: discord.Interaction, produto_id: str, variacao_nome: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
        for i, v in enumerate(variacoes):
            if v["nome"].lower() == variacao_nome.lower():
                variacoes.pop(i)
                salvar_produtos(produtos_disponiveis)
                with estoque_lock:
                    if produto_id in estoque_disponivel and variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                        del estoque_disponivel[produto_id]["variacoes"][variacao_nome]
                        salvar_estoque(estoque_disponivel)
                await interaction.followup.send(f"✅ Variação '{variacao_nome}' removida!", ephemeral=True)
                gc.collect()
                return
        await interaction.followup.send(f"❌ Variação não encontrada!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 16. /set_imagem
@bot.tree.command(name="set_imagem", description="[ADMIN] Definir imagem de um produto")
async def set_imagem(interaction: discord.Interaction, produto_id: str, url_imagem: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        produtos_disponiveis[produto_id]["imagem"] = url_imagem
        salvar_produtos(produtos_disponiveis)
        await interaction.followup.send(f"✅ Imagem definida!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 17. /sincronizar_canal - COMANDO PRINCIPAL CORRIGIDO
@bot.tree.command(name="sincronizar_canal", description="[ADMIN] Atualizar embed de um canal com um produto")
@app_commands.describe(
    canal="O canal onde deseja sincronizar o produto",
    produto_id="O ID do produto que deseja exibir no canal"
)
async def sincronizar_canal(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    
    try:
        if produto_id not in produtos_disponiveis:
            produtos_lista = "\n".join([f"`{pid}` - {p['nome']}" for pid, p in list(produtos_disponiveis.items())[:5]])
            await interaction.followup.send(
                f"❌ Produto `{produto_id}` não encontrado!\n\n📦 **Produtos disponíveis:**\n{produtos_lista}",
                ephemeral=True
            )
            return
        
        p_info = produtos_disponiveis[produto_id]
        embed = await criar_embed_produto_tzada(produto_id, p_info)
        view = ProdutoCompraView(produto_id, p_info["nome"], p_info.get("variacoes", []))
        
        # Limpar mensagens antigas do bot no canal
        contador = 0
        async for msg in canal.history(limit=50):
            if msg.author == bot.user:
                await msg.delete()
                contador += 1
                if contador >= 20:
                    break
        
        mensagem = await canal.send(embed=embed, view=view)
        
        # Salvar referência
        mensagens_canais[str(canal.id)] = {
            "produto_id": produto_id,
            "mensagem_id": mensagem.id,
            "sincronizado_em": datetime.now().isoformat()
        }
        
        embed_resposta = discord.Embed(
            title="✅ CANAL SINCRONIZADO",
            description=f"Canal {canal.mention} atualizado com **{p_info['nome']}**!",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        embed_resposta.add_field(name="📦 Produto", value=p_info['nome'], inline=True)
        embed_resposta.add_field(name="🆔 ID", value=f"`{produto_id}`", inline=True)
        embed_resposta.add_field(name="💰 Preço", value=f"R$ {p_info['preco']:.2f}", inline=True)
        embed_resposta.add_field(name="📊 Estoque", value=f"{verificar_estoque(produto_id)} itens", inline=True)
        embed_resposta.add_field(name="🔄 Variações", value=f"{len(p_info.get('variacoes', []))}", inline=True)
        embed_resposta.add_field(name="🔗 Canal", value=canal.mention, inline=True)
        
        await interaction.followup.send(embed=embed_resposta, ephemeral=True)
        gc.collect()
        
    except discord.Forbidden:
        await interaction.followup.send("❌ Sem permissão para enviar mensagens neste canal!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 18. /ver_estoque
@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver itens no estoque")
async def ver_estoque(interaction: discord.Interaction, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                await interaction.followup.send("📭 Sem estoque!", ephemeral=True)
                return
            itens = estoque_disponivel[produto_id].get("itens", [])
            embed = discord.Embed(title=f"📦 ESTOQUE - {produtos_disponiveis[produto_id]['nome']}", color=0x00ff88)
            if itens:
                embed.add_field(name="📦 Itens", value="\n".join([f"`{item}`" for item in itens[:20]]), inline=False)
            variacoes = estoque_disponivel[produto_id].get("variacoes", {})
            for v_nome, v_itens in list(variacoes.items())[:5]:
                if v_itens:
                    embed.add_field(name=f"🔄 {v_nome}", value="\n".join([f"`{item}`" for item in v_itens[:5]]), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 19. /ver_estoque_variacao
@bot.tree.command(name="ver_estoque_variacao", description="[ADMIN] Ver itens no estoque de uma variação")
async def ver_estoque_variacao(interaction: discord.Interaction, produto_id: str, variacao_nome: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                await interaction.followup.send("📭 Sem estoque!", ephemeral=True)
                return
            variacoes = estoque_disponivel[produto_id].get("variacoes", {})
            if variacao_nome not in variacoes:
                await interaction.followup.send(f"❌ Variação não encontrada!", ephemeral=True)
                return
            itens = variacoes[variacao_nome]
            embed = discord.Embed(title=f"📦 ESTOQUE - {produtos_disponiveis[produto_id]['nome']} - {variacao_nome}", description=f"Total: {len(itens)} itens", color=0x00ff88)
            if itens:
                embed.add_field(name="📦 Itens", value="\n".join([f"`{item}`" for item in itens]), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 20. /configurar_produto
@bot.tree.command(name="configurar_produto", description="[ADMIN] Enviar mensagem de compra")
async def configurar_produto(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
        return
    try:
        p_info = produtos_disponiveis[produto_id]
        embed = await criar_embed_produto_tzada(produto_id, p_info)
        view = ProdutoCompraView(produto_id, p_info["nome"], p_info.get("variacoes", []))
        await canal.send(embed=embed, view=view)
        await interaction.followup.send("✅ Produto configurado no canal!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# 21. /ping
@bot.tree.command(name="ping", description="Verificar se o bot está online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot está online!", ephemeral=True)

# 22. /canais_sincronizados - EXTRA
@bot.tree.command(name="canais_sincronizados", description="[ADMIN] Ver todos os canais sincronizados")
async def canais_sincronizados(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != MEU_ID:
        await interaction.followup.send("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    try:
        if not mensagens_canais:
            await interaction.followup.send("📭 Nenhum canal sincronizado!", ephemeral=True)
            return
        embed = discord.Embed(title="📋 CANAIS SINCRONIZADOS", description=f"Total: {len(mensagens_canais)} canais", color=0x00ff88)
        for canal_id, dados in list(mensagens_canais.items())[:10]:
            canal = bot.get_channel(int(canal_id))
            if canal:
                produto_id = dados.get("produto_id", "N/A")
                p_info = produtos_disponiveis.get(produto_id)
                nome_produto = p_info['nome'] if p_info else "Produto removido"
                embed.add_field(name=f"#{canal.name}", value=f"📦 {nome_produto}\n🆔 `{produto_id}`", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {str(e)}", ephemeral=True)

# ===============================
# WEBHOOK & FLASK
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return f"🤖 G7 STORE ONLINE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json if request.is_json else {}
        payment_id = data.get('invoice_slug')
        ref = data.get('order_nsu', '')
        if not payment_id:
            return "OK", 200
        with webhook_lock:
            if str(payment_id) in pagamentos_processados:
                return "OK", 200
            pagamentos_processados.add(str(payment_id))
            salvar_pagamentos_processados(pagamentos_processados)
            if ref:
                partes = ref.split('_')
                if len(partes) >= 3:
                    p_id = partes[0]
                    u_id = int(partes[-2])
                    v_nome = partes[1] if len(partes) == 4 else None
                    user = bot.get_user(u_id)
                    if not user:
                        try:
                            future = asyncio.run_coroutine_threadsafe(bot.fetch_user(u_id), bot.loop)
                            user = future.result(timeout=10)
                        except:
                            pass
                    if user and p_id in produtos_disponiveis:
                        p_info = produtos_disponiveis[p_id]
                        item = entregar_do_estoque(p_id, v_nome) if p_info.get("tipo") == "auto" else None
                        msg = f"✅ **Sua compra chegou!**\n\n📦 **{p_info['nome']}**\n\n🔐 **Produto:**\n```{item}```" if item else f"✅ Pagamento confirmado para **{p_info['nome']}**! Entrega manual em breve."
                        asyncio.run_coroutine_threadsafe(user.send(msg), bot.loop)
                        asyncio.run_coroutine_threadsafe(log_pagamento_confirmado(user, p_info['nome'], data.get('amount', 0)/100, payment_id, item), bot.loop)
    except:
        pass
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ===============================
# MAIN
# ===============================
if __name__ == "__main__":
    print("🚀 Iniciando Bot G7 STORE...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Flask iniciado")
    gc.collect()
    while True:
        try:
            bot.run(DISCORD_TOKEN)
        except discord.LoginFailure:
            print("❌ Token inválido!")
            break
        except Exception as e:
            print(f"🧨 Erro: {e}. Reiniciando em 30s...")
            time.sleep(30)
