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
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "SEU_TOKEN_AQUI")  # 👈 COLOQUE SEU TOKEN
WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL",
    f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
)

# Arquivos de dados
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

# IDs do Discord (SUBSTITUA PELOS SEUS)
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

produtos_disponiveis = carregar_produtos()
print(f"📦 {len(produtos_disponiveis)} produtos carregados")

# ===============================
# MERCADO PAGO
# ===============================
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    """Gera um pagamento PIX com logs detalhados"""
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

        print(f"🔍 Gerando PIX de R$ {preco_formatado} para {produto_id}...")
        
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
        else:
            print(f"❌ Erro MP: {result}")
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
            print(f"❌ Produto {produto_id} não encontrado")
            return None
        
        if variacao_nome:
            if variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                itens = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
                if itens and len(itens) > 0:
                    item = itens.pop(0)
                    salvar_estoque(estoque_disponivel)
                    return item
            return None
        
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens and len(itens) > 0:
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
        if not canal:
            return None
        
        embed = discord.Embed(
            title="🛒 NOVO CARRINHO ATIVO",
            color=0xffaa00,
            timestamp=datetime.now()
        )
        
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
    except Exception as e:
        print(f"❌ Erro log: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos:
            return
        
        embed = discord.Embed(
            title="✅ PAGAMENTO CONFIRMADO",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        
        if item_entregue:
            embed.add_field(name="🔐 Item Entregue", value=f"```{item_entregue}```", inline=False)
        
        await canal_pagos.send(embed=embed)
        
        # Atualizar carrinho
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
    chave = discord.ui.TextInput(
        label="Chave 2FA",
        placeholder="Cole sua chave aqui",
        min_length=16,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        chave_limpa = self.chave.value.strip().upper()
        totp = pyotp.TOTP(chave_limpa)
        codigo_atual = totp.now()
        tempo_restante = totp.interval - (int(time.time()) % totp.interval)
        
        embed = discord.Embed(
            title="🔐 CÓDIGO 2FA GERADO",
            color=0x00ff88
        )
        embed.add_field(name="📋 CÓDIGO:", value=f"```{codigo_atual}```", inline=False)
        embed.add_field(name="⏰ VÁLIDO POR:", value=f"{tempo_restante} segundos", inline=True)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

class VariacoesView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list):
        super().__init__(timeout=300)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes
        
        options = []
        for i, v in enumerate(variacoes):
            options.append(discord.SelectOption(
                label=v["nome"],
                description=f"R$ {v['preco']:.2f}",
                value=str(i)
            ))
        
        select = discord.ui.Select(placeholder="Escolha uma opção...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            indice = int(interaction.data["values"][0])
            variacao = self.variacoes[indice]
            user = interaction.user
            
            qtd_estoque = verificar_estoque(self.produto_id, variacao["nome"])
            
            if qtd_estoque == 0:
                await interaction.followup.send("❌ Esta opção está esgotada!", ephemeral=True)
                return
            
            pix_data = criar_pagamento_pix_com_preco(
                user.id,
                f"{self.produto_id}_{variacao['nome']}",
                variacao["preco"],
                f"{self.produto_nome} - {variacao['nome']}"
            )
            
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento.", ephemeral=True)
                return
            
            await log_carrinho_ativo(user, pix_data['produto'], pix_data['preco'], pix_data.get('payment_id', 'N/A'))
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
            await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro: {e}")
            await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list = None):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes or []
    
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success)
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            if self.variacoes and len(self.variacoes) > 0:
                view = VariacoesView(self.produto_id, self.produto_nome, self.variacoes)
                await interaction.followup.send(f"📦 **{self.produto_nome}**\n\nSelecione a opção:", view=view, ephemeral=True)
                return
            
            user = interaction.user
            produto_info = produtos_disponiveis[self.produto_id]
            
            qtd_estoque = verificar_estoque(self.produto_id)
            if qtd_estoque == 0 and produto_info.get("tipo") == "auto":
                await interaction.followup.send("❌ Produto esgotado!", ephemeral=True)
                return
            
            pix_data = criar_pagamento_pix_com_preco(user.id, self.produto_id, produto_info["preco"], self.produto_nome)
            
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento.", ephemeral=True)
                return
            
            await log_carrinho_ativo(user, pix_data['produto'], pix_data['preco'], pix_data.get('payment_id', 'N/A'))
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
            await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro: {e}")
            await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)

# ===============================
# COMANDOS DO BOT
# ===============================

@bot.tree.command(name="criar_produto", description="[ADMIN] Criar novo produto")
@app_commands.describe(
    id="ID único",
    nome="Nome do produto",
    preco="Preço em R$",
    descricao="Descrição (use | para benefícios)",
    tipo="auto ou manual"
)
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "auto"):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    if id in produtos_disponiveis:
        await interaction.response.send_message(f"❌ ID {id} já existe!", ephemeral=True)
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
    
    if id not in estoque_disponivel:
        estoque_disponivel[id] = {"itens": [], "variacoes": {}}
        salvar_estoque(estoque_disponivel)
    
    await interaction.response.send_message(f"✅ Produto {nome} criado! ID: `{id}`", ephemeral=True)

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
@app_commands.describe(produto_id="ID do produto", itens="Itens separados por |", variacao="Nome da variação (opcional)")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto {produto_id} não encontrado!", ephemeral=True)
        return
    
    novos_itens = [i.strip() for i in itens.split("|") if i.strip()]
    
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
        
        if variacao:
            if "variacoes" not in estoque_disponivel[produto_id]:
                estoque_disponivel[produto_id]["variacoes"] = {}
            if variacao not in estoque_disponivel[produto_id]["variacoes"]:
                estoque_disponivel[produto_id]["variacoes"][variacao] = []
            estoque_disponivel[produto_id]["variacoes"][variacao].extend(novos_itens)
        else:
            estoque_disponivel[produto_id]["itens"].extend(novos_itens)
            
        salvar_estoque(estoque_disponivel)
    
    await interaction.response.send_message(f"✅ {len(novos_itens)} itens adicionados!", ephemeral=True)

@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver itens no estoque")
@app_commands.describe(produto_id="ID do produto", variacao="Nome da variação")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    if variacao:
        itens = estoque_disponivel.get(produto_id, {}).get("variacoes", {}).get(variacao, [])
    else:
        itens = estoque_disponivel.get(produto_id, {}).get("itens", [])
    
    if not itens:
        await interaction.response.send_message(f"📦 Estoque vazio!", ephemeral=True)
        return
    
    descricao = "\n".join([f"**{i}** - `{item}`" for i, item in enumerate(itens[:20])])
    embed = discord.Embed(title=f"📦 ESTOQUE - {produtos_disponiveis[produto_id]['nome']}", description=descricao, color=0x2b2d31)
    embed.set_footer(text=f"Total: {len(itens)} itens")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
@app_commands.describe(produto_id="ID do produto", nome="Nome da variação", preco="Preço da variação")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    if "variacoes" not in produtos_disponiveis[produto_id]:
        produtos_disponiveis[produto_id]["variacoes"] = []
    
    produtos_disponiveis[produto_id]["variacoes"].append({"nome": nome, "preco": preco})
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(f"✅ Variação {nome} adicionada!", ephemeral=True)

@bot.tree.command(name="configurar_produto", description="[ADMIN] Criar canal do produto")
@app_commands.describe(produto_id="ID do produto", nome_canal="Nome do canal")
async def configurar_produto(interaction: discord.Interaction, produto_id: str, nome_canal: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produto_info = produtos_disponiveis[produto_id]
    guild = interaction.guild
    
    canal = discord.utils.get(guild.channels, name=nome_canal)
    if not canal:
        canal = await guild.create_text_channel(nome_canal)
    
    # Criar embed estilizado
    embed = discord.Embed(
        title=f"⚡ {produto_info['nome']}",
        description=f"**{produto_info['descricao']}**\n\n💰 Preço: R$ {produto_info['preco']:.2f}",
        color=0xffa500
    )
    
    if produto_info.get('imagem'):
        embed.set_image(url=produto_info['imagem'])
    
    view = ProdutoCompraView(produto_id, produto_info['nome'], produto_info.get('variacoes', []))
    
    await canal.purge(limit=10)
    await canal.send(embed=embed, view=view)
    
    await interaction.followup.send(f"✅ Canal {canal.mention} configurado!", ephemeral=True)

@bot.tree.command(name="set_imagem", description="[ADMIN] Definir imagem do produto")
@app_commands.describe(produto_id="ID do produto", url_imagem="URL da imagem")
async def set_imagem(interaction: discord.Interaction, produto_id: str, url_imagem: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
        return
    
    produtos_disponiveis[produto_id]["imagem"] = url_imagem
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(f"✅ Imagem definida!", ephemeral=True)

@bot.tree.command(name="produtos", description="Ver todos os produtos")
async def listar_produtos(interaction: discord.Interaction):
    if not produtos_disponiveis:
        await interaction.response.send_message("📦 Nenhum produto cadastrado!", ephemeral=True)
        return
    
    embed = discord.Embed(title="🛒 G7 STORE - PRODUTOS", color=0x2b2d31)
    
    for key, prod in produtos_disponiveis.items():
        embed.add_field(
            name=f"📦 {prod['nome']}",
            value=f"💰 R$ {prod['preco']:.2f}\n🆔 ID: `{key}`\n📝 {prod.get('descricao', '')[:50]}",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="configurar_2fa", description="[ADMIN] Configurar canal de 2FA")
async def configurar_2fa(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🔐 GERADOR DE CÓDIGO 2FA",
        description="Clique no botão abaixo para gerar seu código 2FA",
        color=0x00ff88
    )
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="🔐 Gerar Código 2FA", style=discord.ButtonStyle.success, custom_id="btn_2fa"))
    
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Canal 2FA configurado!", ephemeral=True)

@bot.tree.command(name="entregar", description="[ADMIN] Entregar produto manual")
@app_commands.describe(usuario="ID do usuário", produto_id="ID do produto")
async def entregar_produto(interaction: discord.Interaction, usuario: str, produto_id: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_id = int(usuario)
        user = await bot.fetch_user(user_id)
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
            return
        
        item = entregar_do_estoque(produto_id)
        
        if not item:
            await interaction.followup.send("❌ Estoque vazio!", ephemeral=True)
            return
        
        await user.send(f"✅ **{produtos_disponiveis[produto_id]['nome']}**\n\n```{item}```")
        await interaction.followup.send(f"✅ Produto entregue para {user.name}!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)

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
    
    if not payment_id:
        return "OK", 200
    
    with webhook_lock:
        if str(payment_id) in pagamentos_processados:
            return "OK", 200
        
        try:
            payment_response = sdk.payment().get(payment_id)
            
            if payment_response["status"] == 200 and payment_response["response"]["status"] == "approved":
                payment = payment_response["response"]
                pagamentos_processados.add(str(payment_id))
                salvar_pagamentos_processados(pagamentos_processados)
                
                ref = payment.get("external_reference", "")
                partes = ref.split('_')
                
                if len(partes) >= 3:
                    user_id = int(partes[-2])
                    produto_id = partes[0]
                    
                    user = bot.get_user(user_id)
                    if not user:
                        future = asyncio.run_coroutine_threadsafe(bot.fetch_user(user_id), bot.loop)
                        user = future.result(timeout=10)
                    
                    if user and produto_id in produtos_disponiveis:
                        produto_info = produtos_disponiveis[produto_id]
                        
                        if produto_info.get("tipo") == "auto":
                            item = entregar_do_estoque(produto_id)
                            
                            if item:
                                asyncio.run_coroutine_threadsafe(
                                    user.send(f"✅ **{produto_info['nome']}**\n\n```{item}```"),
                                    bot.loop
                                )
        except Exception as e:
            print(f"❌ Webhook error: {e}")
    
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# ===============================
# INICIAR
# ===============================
if __name__ == "__main__":
    # Iniciar Flask em thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Iniciar bot
    bot.run(DISCORD_TOKEN)
