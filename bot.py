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

print("🔧 Iniciando bot da G7 STORE (Integrated Edition)...")

# ===============================
# CONFIGURAÇÕES
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
INFINITE_TAG = "guilherme_vinicius90" # Tag original do G7

if not DISCORD_TOKEN:
    print("❌ ERRO: DISCORD_TOKEN não encontrado!")
    # Para ambiente de desenvolvimento, não vamos sair, apenas avisar
    # sys.exit(1)

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    WEBHOOK_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"

# IDs ORIGINAIS DO G7
GUILD_ID = 1472114509068898367
CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
MEU_ID = 1431125477069688953
CARGO_ADMIN = 1472666559049633952 # Cargo do M7 adaptado

# ARQUIVOS
ARQUIVO_PRODUTOS_JSON = "produtos.json"
ARQUIVO_ESTOQUE_JSON = "estoque.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos.json"

# ===============================
# SISTEMA DE PERSISTÊNCIA
# ===============================
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
    except Exception as e:
        print(f"❌ Erro ao salvar {caminho}: {e}")

def salvar_estoque(estoque): salvar_json(ARQUIVO_ESTOQUE_JSON, estoque)
def salvar_produtos(produtos): salvar_json(ARQUIVO_PRODUTOS_JSON, produtos)
def salvar_pagamentos_processados(pagamentos): salvar_json(ARQUIVO_PAGAMENTOS_PROCESSADOS, list(pagamentos))

pagamentos_processados = set(carregar_json(ARQUIVO_PAGAMENTOS_PROCESSADOS, []))
estoque_disponivel = carregar_json(ARQUIVO_ESTOQUE_JSON)
produtos_disponiveis = carregar_json(ARQUIVO_PRODUTOS_JSON)

carrinhos_ativos = {}
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()

# ===============================
# INFINITE PAY GATEWAY
# ===============================
def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    """Gera link de pagamento InfinitePay (Original do G7)"""
    try:
        valor_float = float(preco)
        if valor_float < 1.0:
            return {"erro": "Valor mínimo R$ 1,00."}
        
        preco_centavos = int(round(valor_float * 100))
        
        # Referência externa para identificar no webhook
        # Formato: {produto_id}_{user_id}_{timestamp}
        # Se for variação, o produto_id já virá formatado como produto_variacao
        order_nsu = f"{produto_id}_{user_id}_{int(time.time())}"
        
        payload = {
            "handle": INFINITE_TAG,
            "order_nsu": order_nsu,
            "items": [
                {
                    "quantity": 1, 
                    "price": preco_centavos, 
                    "description": f"Compra: {nome_produto}"[:60]
                }
            ]
        }
        
        if WEBHOOK_URL and WEBHOOK_URL.startswith("https"):
            payload["webhook_url"] = WEBHOOK_URL
            
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        
        print(f"🔍 Gerando link InfinitePay para {nome_produto} (R$ {valor_float:.2f})")
        response = requests.post("https://api.checkout.infinitepay.io/links", json=payload, headers=headers, timeout=15)
        
        if response.status_code in [200, 201]:
            data = response.json()
            return {
                "payment_url": data.get("url"),
                "produto": nome_produto,
                "preco": valor_float,
                "payment_id": data.get("invoice_slug"), # InfinitePay usa invoice_slug como ID
                "produto_id": produto_id
            }
        else:
            print(f"❌ Erro InfinitePay: {response.status_code} - {response.text}")
            return {"erro": f"InfinitePay {response.status_code}"}
            
    except Exception as e:
        print(f"❌ Erro crítico pagamento: {e}")
        return {"erro": str(e)}

# ===============================
# SISTEMA DE ESTOQUE (M7 LOGIC)
# ===============================
def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return None
        
        if variacao_nome:
            variacoes = estoque_disponivel[produto_id].get("variacoes", {})
            if variacao_nome in variacoes:
                itens = variacoes[variacao_nome]
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
            # Se tem variações, a soma total ou específica
            total = 0
            for v in variacoes:
                total += len(estoque_disponivel[produto_id].get("variacoes", {}).get(v["nome"], []))
            return total
            
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# DESIGN DE EMBEDS (TZADA STYLE)
# ===============================
async def criar_embed_produto_tzada(produto_id, p_info):
    qtd = verificar_estoque(produto_id)
    tipo_entrega = "🤖 Entrega Automática!" if p_info.get('tipo') == 'auto' else "👨‍💼 Entrega Manual"
    
    # Formatação da descrição (M7 style com |)
    desc_raw = p_info.get('descricao', 'Sem descrição')
    if '|' in desc_raw:
        beneficios = [b.strip() for b in desc_raw.split('|')]
        desc_formatada = "\n".join([f"✅ {b}" for b in beneficios if b])
    else:
        desc_formatada = f"✅ {desc_raw}"
        
    embed = discord.Embed(
        title=f"⚡ {tipo_entrega}",
        description=f"**{p_info['nome']}**\n\n{desc_formatada}\n\n📦 Estoque: {qtd} unidades",
        color=0xffa500 # Laranja Tzada
    )
    
    if p_info.get('imagem'):
        embed.set_image(url=p_info['imagem'])
        
    embed.add_field(name="💰 Valor", value=f"R$ {p_info['preco']:.2f}", inline=True)
    embed.set_footer(text="G7 STORE - Qualidade e Rapidez")
    return embed

# ===============================
# LOGS E NOTIFICAÇÕES
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal: return None
        
        embed = discord.Embed(
            title="🛒 NOVO CARRINHO ATIVO", 
            color=0xffaa00, 
            timestamp=datetime.now()
        )
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento ID", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="⏳ Aguardando pagamento via InfinitePay...")
        
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {
            "canal": canal.id,
            "mensagem_id": mensagem.id,
            "usuario": user.id,
            "produto": produto_nome
        }
        return mensagem
    except Exception as e:
        print(f"❌ Erro log carrinho: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
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
            embed.set_footer(text="🎉 Entrega realizada com sucesso!")
            await canal_pagos.send(embed=embed)
            
        # Atualizar mensagem do carrinho
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    embed_aprovado = discord.Embed(
                        title="✅ PAGAMENTO APROVADO",
                        description=f"Cliente: {user.mention}\nProduto: {produto_nome}\nValor: R$ {valor:.2f}",
                        color=0x00ff88,
                        timestamp=datetime.now()
                    )
                    await msg.edit(embed=embed_aprovado)
                except: pass
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e:
        print(f"❌ Erro log confirmado: {e}")

# ===============================
# VIEWS E INTERAÇÃO
# ===============================
class VariacoesView(discord.ui.View):
    def __init__(self, produto_id, produto_nome, variacoes):
        super().__init__(timeout=300)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes
        
        options = [
            discord.SelectOption(
                label=v["nome"], 
                description=f"R$ {v['preco']:.2f}", 
                value=str(i)
            ) for i, v in enumerate(variacoes)
        ]
        
        select = discord.ui.Select(
            placeholder="Escolha uma opção...", 
            options=options, 
            custom_id="select_variacao"
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            indice = int(interaction.data["values"][0])
            v = self.variacoes[indice]
            
            # Formato da ref: produto_variacao
            ref_id = f"{self.produto_id}_{v['nome'].replace(' ', '')}"
            
            pix_data = criar_pagamento_pix_com_preco(
                interaction.user.id,
                ref_id,
                v["preco"],
                f"{self.produto_nome} - {v['nome']}"
            )
            
            if "erro" in pix_data:
                await interaction.followup.send(f"❌ Erro ao gerar pagamento: {pix_data['erro']}", ephemeral=True)
                return
                
            embed = discord.Embed(
                title="🧾 PAGAMENTO - G7 STORE",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para realizar o pagamento via InfinitePay.",
                color=0x00ff88
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=pix_data['payment_url']))
            
            await interaction.user.send(embed=embed, view=view)
            await interaction.followup.send("📨 Link de pagamento enviado no seu privado!", ephemeral=True)
            
            # Log do carrinho
            await log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data['payment_id'])
            
        except Exception as e:
            print(f"❌ Erro select callback: {e}")
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
                await interaction.followup.send(
                    "Selecione a opção desejada:", 
                    view=VariacoesView(self.produto_id, self.produto_nome, self.variacoes), 
                    ephemeral=True
                )
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
                await interaction.followup.send(f"❌ Erro ao gerar pagamento: {pix_data['erro']}", ephemeral=True)
                return
                
            embed = discord.Embed(
                title="🧾 PAGAMENTO - G7 STORE",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para realizar o pagamento.",
                color=0x00ff88
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="🔗 Pagar Agora", url=pix_data['payment_url']))
            
            await interaction.user.send(embed=embed, view=view)
            await interaction.followup.send("📨 Link de pagamento enviado no seu privado!", ephemeral=True)
            
            await log_carrinho_ativo(interaction.user, pix_data['produto'], pix_data['preco'], pix_data['payment_id'])
            
        except Exception as e:
            print(f"❌ Erro botão comprar: {e}")
            await interaction.followup.send(f"❌ Erro ao processar compra.", ephemeral=True)

# ===============================
# COMANDOS ADMINISTRATIVOS (M7 STYLE)
# ===============================
class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands sincronizados")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user}")

bot = Bot()

# 1. /criar_produto
@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
async def criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "auto"):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
        
    if id in produtos_disponiveis:
        await interaction.response.send_message("❌ Esse ID já existe!", ephemeral=True)
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
        
    await interaction.response.send_message(f"✅ Produto `{nome}` criado com sucesso!", ephemeral=True)

# 2. /add_estoque
@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
async def add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: str = None):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
        
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
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
        
    await interaction.response.send_message(f"✅ {len(novos_itens)} itens adicionados ao estoque!", ephemeral=True)

# 3. /add_variacao
@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
async def add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
        
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
        return
        
    if "variacoes" not in produtos_disponiveis[produto_id]:
        produtos_disponiveis[produto_id]["variacoes"] = []
        
    produtos_disponiveis[produto_id]["variacoes"].append({"nome": nome, "preco": preco})
    salvar_produtos(produtos_disponiveis)
    
    await interaction.response.send_message(f"✅ Variação `{nome}` adicionada ao produto `{produtos_disponiveis[produto_id]['nome']}`!", ephemeral=True)

# 4. /sincronizar_canal
@bot.tree.command(name="sincronizar_canal", description="[ADMIN] Atualizar embed de um canal com um produto")
async def sincronizar_canal(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
        
    await interaction.response.defer(ephemeral=True)
    
    if produto_id not in produtos_disponiveis:
        await interaction.followup.send("❌ Produto não encontrado!", ephemeral=True)
        return
        
    p_info = produtos_disponiveis[produto_id]
    embed = await criar_embed_produto_tzada(produto_id, p_info)
    view = ProdutoCompraView(produto_id, p_info["nome"], p_info.get("variacoes", []))
    
    # Limpar mensagens antigas do bot no canal
    async for msg in canal.history(limit=50):
        if msg.author == bot.user:
            try: await msg.delete()
            except: pass
            
    await canal.send(embed=embed, view=view)
    await interaction.followup.send(f"✅ Canal {canal.mention} sincronizado com `{p_info['nome']}`!", ephemeral=True)

# 5. /configurar_2fa
@bot.tree.command(name="configurar_2fa", description="[ADMIN] Configurar canal de 2FA com botão")
async def configurar_2fa(interaction: discord.Interaction):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
        
    embed = discord.Embed(
        title="🔐 GERADOR DE CÓDIGO 2FA",
        description="Clique no botão abaixo para gerar seu código 2FA.\n\n1️⃣ Clique em **Gerar Código**\n2️⃣ Cole sua chave secreta\n3️⃣ Receba o código instantaneamente!",
        color=0x00ff88
    )
    
    class Canal2FAView(discord.ui.View):
        def __init__(self): super().__init__(timeout=None)
        @discord.ui.button(label="🔐 Gerar Código 2FA", style=discord.ButtonStyle.primary, custom_id="btn_2fa")
        async def gerar_2fa(self, interaction: discord.Interaction, button: discord.ui.Button):
            class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
                chave = discord.ui.TextInput(label="Chave 2FA", placeholder="Cole sua chave aqui...", min_length=16, required=True)
                async def on_submit(self, interaction: discord.Interaction):
                    try:
                        totp = pyotp.TOTP(self.chave.value.strip().upper())
                        codigo = totp.now()
                        await interaction.response.send_message(f"🔐 Seu código atual é: **{codigo}**", ephemeral=True)
                    except:
                        await interaction.response.send_message("❌ Chave inválida!", ephemeral=True)
            await interaction.response.send_modal(Modal2FA())
            
    await interaction.channel.send(embed=embed, view=Canal2FAView())
    await interaction.response.send_message("✅ Sistema 2FA configurado!", ephemeral=True)

# 6. /limpar
@bot.tree.command(name="limpar", description="[ADMIN] Limpar mensagens do canal")
async def limpar(interaction: discord.Interaction, quantidade: int = 100):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"✅ {len(deleted)} mensagens removidas!", ephemeral=True)

# 7. /entregar (Manual)
@bot.tree.command(name="entregar", description="[ADMIN] Entregar produto manual do estoque")
async def entregar(interaction: discord.Interaction, usuario: discord.User, produto_id: str, variacao_nome: str = None):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    
    item = entregar_do_estoque(produto_id, variacao_nome)
    if not item:
        await interaction.response.send_message("❌ Estoque vazio!", ephemeral=True)
        return
        
    try:
        await usuario.send(f"✅ **Sua compra foi entregue!**\n\n📦 **{produtos_disponiveis[produto_id]['nome']}**\n\n🔐 **Produto:**\n```{item}```")
        await interaction.response.send_message(f"✅ Item entregue para {usuario.mention}!", ephemeral=True)
    except:
        await interaction.response.send_message(f"❌ Não consegui enviar DM para o usuário. Item: `{item}`", ephemeral=True)

# 8. /set_imagem
@bot.tree.command(name="set_imagem", description="[ADMIN] Definir imagem de um produto")
async def set_imagem(interaction: discord.Interaction, produto_id: str, url_imagem: str):
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
        return
    if produto_id not in produtos_disponiveis:
        await interaction.response.send_message("❌ Produto não encontrado!", ephemeral=True)
        return
    produtos_disponiveis[produto_id]["imagem"] = url_imagem
    salvar_produtos(produtos_disponiveis)
    await interaction.response.send_message("✅ Imagem atualizada!", ephemeral=True)

# ===============================
# WEBHOOK SERVER (INFINITE PAY)
# ===============================
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print(f"💰 Webhook recebido: {json.dumps(data)}")
    
    # InfinitePay Webhook Logic
    # status: paid, approved, etc.
    status = data.get("status")
    order_nsu = data.get("order_nsu") # Nossa ref: {produto_id}_{user_id}_{timestamp}
    
    if status == "paid" and order_nsu:
        with webhook_lock:
            invoice_slug = data.get("invoice_slug")
            if invoice_slug in pagamentos_processados:
                return "OK", 200
                
            pagamentos_processados.add(invoice_slug)
            salvar_pagamentos_processados(pagamentos_processados)
            
            # Parsing da referência
            partes = order_nsu.split('_')
            if len(partes) >= 3:
                # Se o produto_id contiver _, precisamos ser cuidadosos
                # Formato sugerido: [produto, user_id, timestamp]
                # Se for variação, o produto_id virá como "produto_variacao"
                user_id = int(partes[-2])
                produto_ref = partes[0]
                
                # Tentar identificar se é uma variação
                # Se o produto_ref não estiver direto em produtos_disponiveis, 
                # pode ser que o produto_id contenha _ ou seja uma variação
                produto_id = None
                variacao_nome = None
                
                if produto_ref in produtos_disponiveis:
                    produto_id = produto_ref
                else:
                    # Busca manual
                    for pid in produtos_disponiveis:
                        if pid in produto_ref:
                            produto_id = pid
                            variacao_nome = produto_ref.replace(pid, "").replace("_", "")
                            break
                
                if produto_id:
                    produto_info = produtos_disponiveis[produto_id]
                    
                    # Entrega
                    if produto_info.get("tipo") == "auto":
                        item = entregar_do_estoque(produto_id, variacao_nome)
                        
                        async def processar_entrega():
                            user = await bot.fetch_user(user_id)
                            if item:
                                try:
                                    await user.send(f"✅ **Pagamento confirmado!**\n\n📦 **{produto_info['nome']}**\n\n🔐 **Seu produto:**\n```{item}```")
                                    await log_pagamento_confirmado(user, produto_info['nome'], float(data.get('amount', 0))/100, invoice_slug, item)
                                except:
                                    canal_pagos = bot.get_channel(CANAL_PAGOS)
                                    if canal_pagos: await canal_pagos.send(f"⚠️ {user.mention}, seu pagamento foi aprovado, mas sua DM está fechada! Chame um admin.")
                            else:
                                try: await user.send(f"✅ **Pagamento confirmado!**\n\n📦 **{produto_info['nome']}**\n\n⚠️ **Estoque esgotado!** Um admin entregará em breve.")
                                except: pass
                                
                        asyncio.run_coroutine_threadsafe(processar_entrega(), bot.loop)
                    else:
                        # Manual
                        async def avisar_manual():
                            user = await bot.fetch_user(user_id)
                            try: await user.send(f"✅ **Pagamento confirmado!**\n\n📦 **{produto_info['nome']}**\n\n⏳ Um administrador entregará seu produto em breve!")
                            except: pass
                        asyncio.run_coroutine_threadsafe(avisar_manual(), bot.loop)
                        
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=5000)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("⚠️ DISCORD_TOKEN não configurado. O bot não iniciará.")
