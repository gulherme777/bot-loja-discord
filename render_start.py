import discord
from discord import app_commands
import requests
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
INFINITE_TAG = "guilherme_vinicius90"

# Se estiver testando localmente com .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", DISCORD_TOKEN)
    
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
CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
MEU_ID = 1431125477069688953
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
# INFINITEPAY
# ===============================


def criar_pagamento_pix_com_preco(user_id, produto_id, preco, nome_produto):
    try:
        # InfinitePay usa valores em centavos
        preco_centavos = int(float(preco) * 100)
        
        payload = {
            "handle": INFINITE_TAG,
            "order_nsu": f"{produto_id}_{user_id}_{int(time.time())}",
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

        print(f"🔍 Gerando link InfinitePay para {nome_produto} (R$ {preco})...")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
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
        else:
            # Retorna o erro formatado para ser exibido
            return {"erro": f"InfinitePay {response.status_code}: {response.text}"}
    except Exception as e:
        return {"erro": f"Exceção: {str(e)}"}

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
        
        # Se pedir uma variação específica
        if variacao_nome:
            return len(estoque_disponivel[produto_id].get("variacoes", {}).get(variacao_nome, []))
        
        # Se o produto tem variações cadastradas, somamos o estoque de todas as variações
        produto_info = produtos_disponiveis.get(produto_id, {})
        variacoes_cadastradas = produto_info.get("variacoes", [])
        
        if variacoes_cadastradas:
            total = 0
            estoque_vars = estoque_disponivel[produto_id].get("variacoes", {})
            for v in variacoes_cadastradas:
                total += len(estoque_vars.get(v["nome"], []))
            return total
            
        # Se não tem variações, retorna o estoque geral
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# LOGS
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal: 
            print(f"❌ Erro log_carrinho_ativo: Canal {CANAL_CARRINHOS} não encontrado")
            return None
            
        embed = discord.Embed(title="🛒 NOVO CARRINHO ATIVO", color=0xffaa00, timestamp=datetime.now())
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        mensagem = await canal.send(embed=embed)
        carrinhos_ativos[str(pagamento_id)] = {"canal": canal.id, "mensagem_id": mensagem.id, "usuario": user.id, "produto": produto_nome}
        return mensagem
    except Exception as e:
        print(f"❌ Erro log_carrinho_ativo: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos:
            print(f"❌ Erro log_pagamento_confirmado: Canal {CANAL_PAGOS} não encontrado")
        else:
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
                except Exception as e: 
                    print(f"❌ Erro ao atualizar mensagem do carrinho: {e}")
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e:
        print(f"❌ Erro log_pagamento_confirmado: {e}")

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
        try:
            await interaction.response.send_message(
                f"```{self.codigo_pix}```", 
                ephemeral=True
            )
        except Exception as e:
            print(f"❌ Erro ao copiar PIX: {e}")
            await interaction.response.send_message("❌ Erro ao copiar PIX", ephemeral=True)

# ===============================
# MODAL PARA 2FA
# ===============================
class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA"):
    chave = discord.ui.TextInput(
        label="Chave 2FA",
        placeholder="Cole sua chave aqui (ex: 7J64V3P3E77J3LKN...)",
        min_length=16,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            
            chave_limpa = self.chave.value.strip().upper()
            totp = pyotp.TOTP(chave_limpa)
            codigo_atual = totp.now()
            tempo_restante = totp.interval - (int(time.time()) % totp.interval)
            
            embed = discord.Embed(
                title="🔐 **CÓDIGO 2FA GERADO**",
                description="Use o código abaixo para acessar sua conta:",
                color=0x00ff88,
                timestamp=datetime.now()
            )
            embed.add_field(name="📋 **CÓDIGO:**", value=f"```{codigo_atual}```", inline=False)
            embed.add_field(name="⏰ **VÁLIDO POR:**", value=f"{tempo_restante} segundos", inline=True)
            embed.add_field(name="🔑 **SUA CHAVE:**", value=f"||{chave_limpa}||", inline=False)
            embed.set_footer(text="O código expira em 30 segundos.")
            
            # Botão para copiar o código gerado
            class CopiarCodigoView(discord.ui.View):
                def __init__(self, codigo: str):
                    super().__init__(timeout=60)
                    self.codigo = codigo
                @discord.ui.button(label="📋 Copiar Código", style=discord.ButtonStyle.success)
                async def copiar(self, i: discord.Interaction, b: discord.ui.Button):
                    try:
                        await i.response.send_message(f"{self.codigo}", ephemeral=True)
                    except Exception as e:
                        print(f"❌ Erro ao copiar: {e}")

            await interaction.followup.send(embed=embed, view=CopiarCodigoView(codigo_atual), ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao gerar código 2FA: {e}")
            try:
                await interaction.followup.send(f"❌ Erro ao gerar código: {e}", ephemeral=True)
            except:
                pass

# ===============================
# VIEW PARA O CANAL 2FA
# ===============================
class Canal2FAView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔐 Gerar Código 2FA", style=discord.ButtonStyle.success, custom_id="btn_gerar_2fa")
    async def gerar_2fa_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(Modal2FA())
        except Exception as e:
            print(f"❌ Erro ao abrir modal 2FA: {e}")
            await interaction.response.send_message("❌ Erro ao abrir modal", ephemeral=True)

# ===============================
# CLASSE DO MENU DE VARIAÇÕES
# ===============================
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
            variacao = self.variacoes[indice]
            user = interaction.user
            
            qtd_estoque = verificar_estoque(self.produto_id, variacao["nome"])
            produto_info = produtos_disponiveis[self.produto_id]
            
            if qtd_estoque == 0 and produto_info.get("tipo") == "auto":
                await interaction.followup.send(
                    f"❌ **{variacao['nome']} está esgotado!** Aguarde reposição.",
                    ephemeral=True
                )
                return
            
            try:
                # O formato da ref será: PRODUTOID_VARIACAONOME_USERID_TIMESTAMP
                pix_data = criar_pagamento_pix_com_preco(
                    user.id,
                    f"{self.produto_id}_{variacao['nome']}",
                    variacao["preco"],
                    f"{self.produto_nome} - {variacao['nome']}"
                )
            except Exception as e:
                await interaction.followup.send(f"❌ Erro Técnico: {e}", ephemeral=True)
                return
            
            if not pix_data or "erro" in pix_data:
                msg_erro = pix_data["erro"] if pix_data and "erro" in pix_data else "Erro desconhecido"
                await interaction.followup.send(f"❌ Erro ao gerar pagamento: `{msg_erro}`", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO - G7 STORE",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para pagar via **PIX** ou **Cartão**.",
                color=0x00ff88
            )
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            class PagarView(discord.ui.View):
                def __init__(self, url):
                    super().__init__(timeout=300)
                    self.add_item(discord.ui.Button(label="🔗 Pagar Agora (InfinitePay)", url=url))
            
            await user.send(embed=embed_pix, view=PagarView(pix_data['payment_url']))
            await interaction.followup.send("📨 Link de pagamento enviado no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao processar variação: {e}")
            try:
                await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)
            except:
                pass


# ===============================
# NOVO DESIGN DE PRODUTO - ESTILO TZADA STORE
# ===============================
async def criar_embed_produto_tzada(produto_id: str, produto_info: dict):
    """Cria um único embed estilo Tzada Store com imagem no topo e texto embaixo"""
    try:
        imagem_url = produto_info.get('imagem', '')
        qtd_variacoes = len(produto_info.get("variacoes", []))
        qtd_estoque = verificar_estoque(produto_id)
        tipo_entrega = "🤖 Entrega Automática!" if produto_info.get('tipo') == 'auto' else "👨‍💼 Entrega Manual"
        
        # Construir descrição com benefícios (estilo Tzada)
        descricao = produto_info.get('descricao', 'Sem descrição')
        
        # Se houver benefícios (separados por |), formatá-los com checkmarks
        if '|' in descricao:
            beneficios = [b.strip() for b in descricao.split('|')]
            descricao_formatada = "\n".join([f"✅ {b}" for b in beneficios if b])
        else:
            descricao_formatada = f"✅ {descricao}"
        
        # Adicionar informações de estoque
        estoque_info = ""
        if produto_info.get('tipo') == 'auto':
            estoque_info = f"\n📦 Estoque: {qtd_estoque} unidades"
        
        # ✅ CRIAR UM Único EMBED COM IMAGEM NO TOPO
        embed = discord.Embed(
            color=0xffa500  # Laranja vibrante como Tzada
        )
        
        # ✅ ADICIONAR IMAGEM COMO THUMBNAIL (PEQUENA NO CANTO)
        # Depois vamos usar set_image para forçar no topo
        if imagem_url and imagem_url != "":
            # Usar set_image para forçar a imagem no topo
            embed.set_image(url=imagem_url)
        
        # ✅ ADICIONAR TÍTULO E DESCRIÇÃO
        embed.title = f"⚡ {tipo_entrega}"
        embed.description = f"**{produto_info['nome']}**\n\n{descricao_formatada}{estoque_info}"
        
        # Campos de Valor e Estoque lado a lado
        embed.add_field(
            name="💰 Valor à vista",
            value=f"R$ {produto_info['preco']:.2f}",
            inline=True
        )
        
        if produto_info.get('tipo') == 'auto':
            embed.add_field(
                name="📦 Restam",
                value=f"{qtd_estoque}",
                inline=True
            )
        
        # Adicionar variações se houver
        if qtd_variacoes > 0:
            embed.add_field(
                name="🎮 Opções Disponíveis",
                value=f"{qtd_variacoes} variações",
                inline=True
            )
        
        embed.set_footer(text="G7 STORE - Clique no botão abaixo para comprar!")
        embed.timestamp = datetime.now()
        
        return embed  # Retorna um único embed
    except Exception as e:
        print(f"❌ Erro ao criar embed Tzada: {e}")
        return None

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list = None):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes or []
    
    @discord.ui.button(label="🛒 Comprar", style=discord.ButtonStyle.success, custom_id="btn_comprar")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            if self.variacoes and len(self.variacoes) > 0:
                view = VariacoesView(self.produto_id, self.produto_nome, self.variacoes)
                await interaction.followup.send(
                    f"📦 **{self.produto_nome}**\n\nSelecione a opção desejada:",
                    view=view,
                    ephemeral=True
                )
                return
            
            user = interaction.user
            
            produto_info = produtos_disponiveis[self.produto_id]
            
            qtd_estoque = verificar_estoque(self.produto_id)
            if qtd_estoque == 0 and produto_info.get("tipo") == "auto":
                await interaction.followup.send("❌ **Produto esgotado!** Aguarde reposição.", ephemeral=True)
                return
            
            try:
                pix_data = criar_pagamento_pix_com_preco(user.id, self.produto_id, produto_info["preco"], self.produto_nome)
            except Exception as e:
                await interaction.followup.send(f"❌ Erro Técnico: {e}", ephemeral=True)
                return
            
            if not pix_data or "erro" in pix_data:
                msg_erro = pix_data["erro"] if pix_data and "erro" in pix_data else "Erro desconhecido"
                await interaction.followup.send(f"❌ Erro ao gerar pagamento: `{msg_erro}`", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO - G7 STORE",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}\n\nClique no botão abaixo para pagar via **PIX** ou **Cartão**.",
                color=0x00ff88
            )
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            class PagarView(discord.ui.View):
                def __init__(self, url):
                    super().__init__(timeout=300)
                    self.add_item(discord.ui.Button(label="🔗 Pagar Agora (InfinitePay)", url=url))
            
            await user.send(embed=embed_pix, view=PagarView(pix_data['payment_url']))
            await interaction.followup.send("📨 Link de pagamento enviado no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao processar compra: {e}")
            try:
                await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)
            except:
                pass


# ===============================
# COMANDOS
# ===============================
@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
@app_commands.describe(
    produto_id="ID do produto",
    itens="Itens separados por | (ex: conta1:senha1 | conta2:senha2)",
    variacao_indice="Índice da variação (veja em /listar_variacoes) ou deixe vazio para estoque geral"
)
async def add_estoque(
    interaction: discord.Interaction,
    produto_id: str,
    itens: str,
    variacao_indice: int = -1
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        variacao_nome = None
        
        # Se um índice foi fornecido, buscar o nome da variação
        if variacao_indice != -1:
            variacoes = produto.get("variacoes", [])
            if 0 <= variacao_indice < len(variacoes):
                variacao_nome = variacoes[variacao_indice]["nome"]
            else:
                await interaction.response.send_message(f"❌ Índice de variação `{variacao_indice}` inválido! Use `/listar_variacoes` para ver os índices.", ephemeral=True)
                return

        novos_itens = [i.strip() for i in itens.split("|") if i.strip()]
        
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
            
            if variacao_nome:
                if "variacoes" not in estoque_disponivel[produto_id]:
                    estoque_disponivel[produto_id]["variacoes"] = {}
                
                if variacao_nome not in estoque_disponivel[produto_id]["variacoes"]:
                    estoque_disponivel[produto_id]["variacoes"][variacao_nome] = []
                
                estoque_disponivel[produto_id]["variacoes"][variacao_nome].extend(novos_itens)
            else:
                estoque_disponivel[produto_id]["itens"].extend(novos_itens)
                
            salvar_estoque(estoque_disponivel)
        
        local = f"na variação `{variacao_nome}`" if variacao_nome else "no estoque geral"
        await interaction.response.send_message(f"✅ {len(novos_itens)} itens adicionados {local} para `{produto['nome']}`!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao adicionar estoque: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver itens no estoque")
@app_commands.describe(produto_id="ID do produto", variacao="Nome da variação (opcional)")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        
        if variacao:
            itens = estoque_disponivel.get(produto_id, {}).get("variacoes", {}).get(variacao, [])
        else:
            itens = estoque_disponivel.get(produto_id, {}).get("itens", [])
        
        if not itens:
            await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nEstoque vazio!", ephemeral=True)
            return
        
        descricao = ""
        for i, item in enumerate(itens):
            descricao += f"**{i}** - `{item}`\n"
        
        embed = discord.Embed(
            title=f"📦 ESTOQUE - {produto['nome']}",
            description=descricao,
            color=0x2b2d31
        )
        embed.set_footer(text=f"Total: {len(itens)} itens | Use /remover_estoque com o índice")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao ver estoque: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="ver_estoque_variacao", description="[ADMIN] Ver itens no estoque de uma variação específica")
@app_commands.describe(produto_id="ID do produto", variacao="Nome exato da variação")
async def ver_estoque_variacao(interaction: discord.Interaction, produto_id: str, variacao: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
            
        itens = estoque_disponivel.get(produto_id, {}).get("variacoes", {}).get(variacao, [])
        
        if not itens:
            await interaction.response.send_message(f"📦 **{variacao}**\n\nEstoque vazio!", ephemeral=True)
            return
        
        descricao = ""
        for i, item in enumerate(itens):
            descricao += f"**Índice: `{i}`** - `{item}`\n"
        
        embed = discord.Embed(
            title=f"📦 ESTOQUE - {variacao}",
            description=descricao,
            color=0x2b2d31
        )
        embed.set_footer(text=f"Total: {len(itens)} itens | Use /remover_estoque com este índice e nome da variação")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao ver estoque da variação: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
@app_commands.describe(
    produto_id="ID do produto",
    nome="Nome da variação (ex: Completo, Apenas Conta, Premium)",
    preco="Preço da variação em R$"
)
async def add_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    nome: str,
    preco: float
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        if "variacoes" not in produtos_disponiveis[produto_id]:
            produtos_disponiveis[produto_id]["variacoes"] = []
        
        produtos_disponiveis[produto_id]["variacoes"].append({
            "nome": nome,
            "preco": preco
        })
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Variação adicionada!\n\n"
            f"📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
            f"🎮 Opção: {nome}\n"
            f"💰 Preço: R$ {preco:.2f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao adicionar variação: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="listar_variacoes", description="[ADMIN] Listar variações de um produto")
@app_commands.describe(produto_id="ID do produto")
async def listar_variacoes(interaction: discord.Interaction, produto_id: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        variacoes = produto.get("variacoes", [])
        
        if not variacoes:
            await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nNenhuma variação encontrada!", ephemeral=True)
            return
        
        descricao = ""
        for i, v in enumerate(variacoes):
            qtd = verificar_estoque(produto_id, v["nome"])
            descricao += f"**Índice: `{i}`** | Nome: `{v['nome']}` | Preço: R$ {v['preco']:.2f} | Estoque: `{qtd}`\n"
        
        embed = discord.Embed(
            title=f"🎮 VARIAÇÕES - {produto['nome']}",
            description=descricao,
            color=0x2b2d31
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao listar variações: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="remover_variacao", description="[ADMIN] Remover uma variação de um produto")
@app_commands.describe(produto_id="ID do produto", indice="Índice da variação (veja com /listar_variacoes)")
async def remover_variacao(interaction: discord.Interaction, produto_id: str, indice: int):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        variacoes = produto.get("variacoes", [])
        
        if indice < 0 or indice >= len(variacoes):
            await interaction.response.send_message(f"❌ Índice `{indice}` inválido!", ephemeral=True)
            return
        
        removida = variacoes.pop(indice)
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(f"✅ Variação `{removida['nome']}` removida do produto `{produto['nome']}`!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao remover variação: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="remover_estoque", description="🗑️ Remove itens específicos do estoque de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Número do item a remover (veja com /ver_estoque)",
    variacao="Nome da variação (deixe em branco para produto sem variações)"
)
async def remover_estoque(interaction: discord.Interaction, produto_id: str, indice: int, variacao: str = None):
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Verificar se é admin
        if interaction.user.id != MEU_ID and CARGO_ADMIN not in [role.id for role in interaction.user.roles]:
            await interaction.followup.send("❌ Apenas administradores podem remover estoque!", ephemeral=True)
            return
        
        # Verificar se o produto existe
        if produto_id not in estoque_disponivel:
            await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        with estoque_lock:
            produto_estoque = estoque_disponivel[produto_id]
            
            # Se tem variação
            if variacao:
                if variacao not in produto_estoque.get("variacoes", {}):
                    await interaction.followup.send(f"❌ Variação `{variacao}` não encontrada para o produto `{produto_id}`!", ephemeral=True)
                    return
                
                lista_itens = produto_estoque["variacoes"][variacao]
                
                # Validar o índice
                if indice < 0 or indice >= len(lista_itens):
                    await interaction.followup.send(
                        f"❌ Índice `{indice}` inválido! O estoque tem apenas **{len(lista_itens)}** itens (0 a {len(lista_itens)-1}).",
                        ephemeral=True
                    )
                    return
                
                # Remover o item específico
                item_removido = lista_itens.pop(indice)
                salvar_estoque(estoque_disponivel)
                
                await interaction.followup.send(
                    f"✅ Item **#{indice}** removido da variação `{variacao}` do produto `{produto_id}`!\n"
                    f"🗑️ Item removido: `{item_removido}`\n"
                    f"📦 Estoque restante: **{len(lista_itens)}** itens",
                    ephemeral=True
                )
            else:
                # Sem variação
                lista_itens = produto_estoque.get("itens", [])
                
                # Validar o índice
                if indice < 0 or indice >= len(lista_itens):
                    await interaction.followup.send(
                        f"❌ Índice `{indice}` inválido! O estoque tem apenas **{len(lista_itens)}** itens (0 a {len(lista_itens)-1}).",
                        ephemeral=True
                    )
                    return
                
                # Remover o item específico
                item_removido = lista_itens.pop(indice)
                salvar_estoque(estoque_disponivel)
                
                await interaction.followup.send(
                    f"✅ Item **#{indice}** removido do produto `{produto_id}`!\n"
                    f"🗑️ Item removido: `{item_removido}`\n"
                    f"📦 Estoque restante: **{len(lista_itens)}** itens",
                    ephemeral=True
                )
    except Exception as e:
        print(f"❌ Erro ao remover estoque: {e}")
        await interaction.followup.send(f"❌ Erro ao remover estoque: {e}", ephemeral=True)


@bot.tree.command(name="sincronizar_canal", description="[ADMIN] Atualizar embed de um canal existente")
@app_commands.describe(produto_id="ID do produto")
async def sincronizar_canal(interaction: discord.Interaction, produto_id: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto_info = produtos_disponiveis[produto_id]
        canal = interaction.channel
        
        embed = await criar_embed_produto_tzada(produto_id, produto_info)
        if not embed:
            await interaction.followup.send("❌ Erro ao criar embed do produto.", ephemeral=True)
            return
            
        view = ProdutoCompraView(produto_id, produto_info['nome'], produto_info.get('variacoes', []))
        
        # Limpar mensagens do canal
        try:
            await canal.purge(limit=100)
        except:
            pass
            
        await canal.send(embed=embed, view=view)
        
        await interaction.followup.send(f"✅ Canal sincronizado!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao sincronizar canal: {e}")
        try:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)
        except:
            pass

@bot.tree.command(name="limpar", description="[ADMIN] Limpar mensagens do canal")
@app_commands.describe(quantidade="Quantidade de mensagens a limpar")
async def limpar_chat(interaction: discord.Interaction, quantidade: int = 100):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            deleted = await interaction.channel.purge(limit=quantidade)
            await interaction.followup.send(f"✅ Foram removidas `{len(deleted)}` mensagens!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Erro ao limpar canal: {e}", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro no comando limpar: {e}")

@bot.tree.command(name="configurar_2fa", description="[ADMIN] Configurar canal de 2FA com botão")
async def configurar_2fa(interaction: discord.Interaction):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🔐 GERADOR DE CÓDIGO 2FA",
            description="Clique no botão abaixo para gerar seu código 2FA de forma rápida e segura.\n\n"
                        "1️⃣ Clique em **Gerar Código 2FA**\n"
                        "2️⃣ Cole sua chave secreta\n"
                        "3️⃣ O bot enviará o código atual para você!",
            color=0x00ff88
        )
        embed.set_footer(text="G7 STORE - Segurança em primeiro lugar")
        
        await interaction.channel.send(embed=embed, view=Canal2FAView())
        await interaction.response.send_message("✅ Canal de 2FA configurado!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao configurar 2FA: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="set_imagem", description="[ADMIN] Definir imagem de um produto")
@app_commands.describe(produto_id="ID do produto", url_imagem="URL da imagem")
async def set_imagem(interaction: discord.Interaction, produto_id: str, url_imagem: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produtos_disponiveis[produto_id]["imagem"] = url_imagem
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Imagem atualizada!\n🖼️ Nova imagem: {url_imagem}\n\n💡 Use `/sincronizar_canal {produto_id}` para aplicar.",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao definir imagem: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
@app_commands.describe(
    id="ID único do produto",
    nome="Nome do produto",
    preco="Preço em R$",
    descricao="Descrição do produto (use | para separar benefícios)",
    tipo="Tipo: auto or manual"
)
async def criar_produto(
    interaction: discord.Interaction,
    id: str,
    nome: str,
    preco: float,
    descricao: str,
    tipo: str = "auto"
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if id in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto com ID `{id}` já existe!", ephemeral=True)
            return
        
        if tipo not in ["auto", "manual"]:
            await interaction.response.send_message("❌ Tipo deve ser `auto` ou `manual`", ephemeral=True)
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
        
        tipo_texto = "🤖 Entrega automática" if tipo == "auto" else "👨‍💼 Entrega manual"
        
        await interaction.response.send_message(
            f"✅ Produto criado!\n\n📦 ID: `{id}`\n📝 Nome: {nome}\n💰 Preço: R$ {preco:.2f}\n🎮 Tipo: {tipo_texto}\n\n💡 Use `/add_estoque` para adicionar itens!\n💡 Use `/add_variacao` para adicionar opções!\n💡 Use `/configurar_produto {id} {id}` para criar o canal!",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao criar produto: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="listar_produtos", description="[ADMIN] Listar todos os produtos")
async def listar_produtos(interaction: discord.Interaction):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if not produtos_disponiveis:
            await interaction.response.send_message("❌ Nenhum produto cadastrado!", ephemeral=True)
            return
            
        descricao = ""
        for pid, pinfo in produtos_disponiveis.items():
            estoque = verificar_estoque(pid)
            descricao += f"🆔 `{pid}` | **{pinfo['nome']}** | R$ {pinfo['preco']:.2f} | 📦 {estoque}\n"
            
        embed = discord.Embed(title="📦 PRODUTOS CADASTRADOS", description=descricao, color=0x2b2d31)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao listar produtos: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="editar_preco", description="[ADMIN] Alterar preço de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    novo_preco="Novo preço em R$"
)
async def editar_preco(interaction: discord.Interaction, produto_id: str, novo_preco: float):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        preco_antigo = produto["preco"]
        produto["preco"] = novo_preco
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Preço atualizado!\n📦 Produto: {produto['nome']}\n📉 Antigo: R$ {preco_antigo:.2f}\n📈 Novo: R$ {novo_preco:.2f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao editar preço: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="editar_produto", description="[ADMIN] Alterar nome/descrição")
@app_commands.describe(
    produto_id="ID do produto",
    novo_nome="Novo nome (opcional)",
    nova_descricao="Nova descrição (opcional)"
)
async def editar_produto(
    interaction: discord.Interaction, 
    produto_id: str, 
    novo_nome: str = None, 
    nova_descricao: str = None
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        mensagem = f"✅ Produto atualizado!\n\n📦 ID: `{produto_id}`\n"
        
        if novo_nome:
            mensagem += f"📝 Nome: {produto['nome']} → {novo_nome}\n"
            produto["nome"] = novo_nome
        
        if nova_descricao:
            mensagem += f"📄 Descrição atualizada\n"
            produto["descricao"] = nova_descricao
        
        salvar_produtos(produtos_disponiveis)
        await interaction.response.send_message(mensagem, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao editar produto: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="remover_produto", description="[ADMIN] Remover um produto")
@app_commands.describe(produto_id="ID do produto")
async def remover_produto(interaction: discord.Interaction, produto_id: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis.pop(produto_id)
        salvar_produtos(produtos_disponiveis)
        
        # Também remover do estoque se quiser limpar tudo
        if produto_id in estoque_disponivel:
            estoque_disponivel.pop(produto_id)
            salvar_estoque(estoque_disponivel)
        
        await interaction.response.send_message(f"✅ Produto removido!\n📦 Removido: {produto['nome']}", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao remover produto: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="entregar", description="[ADMIN] Entregar produto manual do estoque")
@app_commands.describe(
    usuario="ID do usuário",
    produto_id="ID do produto",
    indice="Índice do item no estoque (opcional, use /ver_estoque para ver)"
)
async def entregar_produto(
    interaction: discord.Interaction, 
    usuario: str, 
    produto_id: str,
    indice: int = -1
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        user_id = int(usuario)
        user = await bot.fetch_user(user_id)
        
        if not user:
            await interaction.followup.send("❌ Usuário não encontrado.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto não encontrado!", ephemeral=True)
            return
        
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
            
            itens = estoque_disponivel[produto_id].get("itens", [])
            
            if not itens:
                await interaction.followup.send(f"❌ **Estoque vazio para {produtos_disponiveis[produto_id]['nome']}!**\n\nUse `/add_estoque` para adicionar itens.", ephemeral=True)
                return
            
            if indice == -1:
                item = itens.pop(0)
            else:
                if indice < 0 or indice >= len(itens):
                    await interaction.followup.send(f"❌ Índice inválido! Use 0 a {len(itens)-1} ou /ver_estoque para ver os índices.", ephemeral=True)
                    return
                item = itens.pop(indice)
            
            salvar_estoque(estoque_disponivel)
        
        produto = produtos_disponiveis[produto_id]
        
        await user.send(
            f"🎮 **Sua {produto['nome']} chegou!**\n\n"
            f"```{item}```\n\n"
            "✅ Obrigado pela preferência!"
        )
        
        await interaction.followup.send(f"✅ **{produto['nome']} entregue para {user.name}!**\n🔐 Item: `{item}`\n📊 Restam {len(estoque_disponivel[produto_id].get('itens', []))} itens em estoque.", ephemeral=True)
        
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="📦 PRODUTO ENTREGUE",
                color=0x3498db,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Cliente", value=user.mention, inline=True)
            embed.add_field(name="📦 Produto", value=produto['nome'], inline=True)
            embed.add_field(name="🔐 Item", value=f"`{item}`", inline=False)
            embed.set_footer(text=f"Entregue por: {interaction.user.name}")
            await canal_pagos.send(embed=embed)
    except ValueError:
        await interaction.followup.send("❌ ID inválido.", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao entregar: {e}")
        try:
            await interaction.followup.send(f"❌ Erro: {e}", ephemeral=True)
        except:
            pass

@bot.tree.command(name="backup", description="[ADMIN] Fazer backup dos produtos")
async def fazer_backup(interaction: discord.Interaction):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        backup_data = json.dumps(produtos_disponiveis, indent=2, ensure_ascii=False)
        import io
        file = discord.File(io.StringIO(backup_data), filename="backup_produtos.json")
        
        await interaction.response.send_message(
            "✅ Backup realizado! Guarde este arquivo.",
            file=file,
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao fazer backup: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="2fa", description="Gerar código 2FA a partir da chave")
@app_commands.describe(chave="Sua chave 2FA (ex: 7J64V3P3E77J3LKNUGSZ5QANTLRLTKVL)")
async def gerar_2fa(interaction: discord.Interaction, chave: str):
    """Gera o código 2FA atual a partir da chave fornecida"""
    try:
        await interaction.response.defer(ephemeral=True)
        
        chave = chave.strip().upper()
        if len(chave) < 16:
            embed = discord.Embed(
                title="❌ **CHAVE INVÁLIDA**",
                description="A chave deve ter pelo menos 16 caracteres.",
                color=0xff0000,
                timestamp=datetime.now()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        totp = pyotp.TOTP(chave)
        codigo_atual = totp.now()
        tempo_restante = totp.interval - (int(time.time()) % totp.interval)
        
        embed = discord.Embed(
            title="🔐 **CÓDIGO 2FA GERADO**",
            description="Use o código abaixo para acessar sua conta:",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        embed.add_field(name="📋 **CÓDIGO:**", value=f"```{codigo_atual}```", inline=False)
        embed.add_field(name="⏰ **VÁLIDO POR:**", value=f"{tempo_restante} segundos", inline=True)
        embed.add_field(name="🔑 **SUA CHAVE:**", value=f"||{chave}||", inline=False)
        embed.set_footer(text="O código expira em 30 segundos.")
        
        # Botão para copiar o código gerado
        class CopiarCodigoView(discord.ui.View):
            def __init__(self, codigo: str):
                super().__init__(timeout=60)
                self.codigo = codigo
            @discord.ui.button(label="📋 Copiar Código", style=discord.ButtonStyle.success)
            async def copiar(self, i: discord.Interaction, b: discord.ui.Button):
                try:
                    await i.response.send_message(f"{self.codigo}", ephemeral=True)
                except Exception as e:
                    print(f"❌ Erro ao copiar: {e}")

        await interaction.followup.send(embed=embed, view=CopiarCodigoView(codigo_atual), ephemeral=True)
    except Exception as e:
        print(f"❌ Erro 2FA: {e}")
        try:
            await interaction.followup.send("❌ Erro ao gerar código. Verifique a chave.", ephemeral=True)
        except:
            pass


# ===============================
# WEBHOOK
# ===============================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 G7 STORE - Bot está online e funcionando!", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    print("\n" + "⚡" * 20)
    print(f"WEBHOOK INFINITEPAY RECEBIDO ÀS {datetime.now().strftime('%H:%M:%S')}")
    
    data = request.json if request.is_json else {}
    print(f"📩 Dados: {json.dumps(data, indent=2)}")
    
    # Formato InfinitePay: { "invoice_slug": "...", "order_nsu": "...", "amount": ... }
    payment_id = data.get('invoice_slug')
    ref = data.get('order_nsu', '')
    
    if not payment_id:
        return "OK", 200

    with webhook_lock:
        if str(payment_id) in pagamentos_processados:
            return "OK", 200
        
        try:
            # InfinitePay envia o webhook apenas quando aprovado
            print(f"✅ Pagamento {payment_id} APROVADO na InfinitePay!")
            
            pagamentos_processados.add(str(payment_id))
            salvar_pagamentos_processados(pagamentos_processados)
            
            if ref:
                partes = ref.split('_')
                if len(partes) >= 3:
                    produto_id = partes[0]
                    user_id = int(partes[-2])
                    
                    user = bot.get_user(user_id)
                    if not user:
                        try:
                            future = asyncio.run_coroutine_threadsafe(bot.fetch_user(user_id), bot.loop)
                            user = future.result(timeout=10)
                        except: pass
                    
                    if user and produto_id in produtos_disponiveis:
                        produto_info = produtos_disponiveis[produto_id]
                        
                        # Se tiver variação na referência (formato: PRODUTO_VARIACAO_USER_TIME)
                        # O ref pode ter múltiplos '_' se o produto_id ou variacao_nome contiverem '_'
                        # Mas pela lógica de criação: f"{produto_id}_{user_id}_{int(time.time())}" 
                        # ou f"{self.produto_id}_{variacao['nome']}_{user.id}_{int(time.time())}"
                        
                        variacao_nome = None
                        # Tentar extrair a variação se houver mais de 3 partes
                        if len(partes) >= 4:
                            # O produto_id é o primeiro, user_id é o penúltimo, time é o último
                            # Tudo entre o primeiro e o penúltimo é a variação
                            variacao_nome = "_".join(partes[1:-2])
                        
                        if produto_info.get("tipo") == "auto":
                            item = entregar_do_estoque(produto_id, variacao_nome=variacao_nome)
                            if item:
                                async def enviar_entrega():
                                    try:
                                        await user.send(f"✅ **Pagamento confirmado!**\n\n📦 **{produto_info['nome']}**\n\n🔐 **Seu produto:**\n```{item}```\n\n✅ Obrigado pela preferência!")
                                        await log_pagamento_confirmado(user, produto_info['nome'], data.get('amount', 0)/100, payment_id, item)
                                    except: pass
                                asyncio.run_coroutine_threadsafe(enviar_entrega(), bot.loop)
                            else:
                                asyncio.run_coroutine_threadsafe(user.send("✅ Pagamento confirmado, mas o estoque acabou! Um admin vai te entregar em breve."), bot.loop)
                        else:
                            asyncio.run_coroutine_threadsafe(user.send(f"✅ Pagamento confirmado para **{produto_info['nome']}**! Um administrador fará a entrega manual em breve."), bot.loop)
        except Exception as e:
            print(f"❌ Erro Webhook: {e}")
            
    return "OK", 200

# ===============================
# INICIAR BOT E SERVIDOR FLASK
# ===============================

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    # Inicia Flask em uma thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Inicia o bot Discord
    bot.run(DISCORD_TOKEN)
