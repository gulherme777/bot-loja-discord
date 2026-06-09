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


CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852

MEU_ID = 1431125477069688953
CARGO_ADMIN = 1431125477069688953

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
# FUNÇÃO PARA ENTREGAR PRODUTO DO ESTOQUE
# ===============================

def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            print(f"❌ Produto {produto_id} não encontrado no estoque")
            return None
        
        if variacao_nome:
            if variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                itens = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
                if itens and len(itens) > 0:
                    item = itens.pop(0)
                    salvar_estoque(estoque_disponivel)
                    print(f"✅ Entregue da variação {variacao_nome}: {item}")
                    return item
                else:
                    print(f"⚠️ Estoque vazio para variação {variacao_nome}")
                    return None
            else:
                print(f"⚠️ Variação {variacao_nome} não encontrada")
                return None
        
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens and len(itens) > 0:
            item = itens.pop(0)
            salvar_estoque(estoque_disponivel)
            print(f"✅ Entregue do estoque geral: {item}")
            return item
        
        print(f"⚠️ Estoque vazio para {produto_id}")
        return None

def verificar_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            return 0
        
        if variacao_nome and variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
            return len(estoque_disponivel[produto_id]["variacoes"][variacao_nome])
        
        return len(estoque_disponivel[produto_id].get("itens", []))

# ===============================
# FUNÇÕES DE LOG
# ===============================
async def log_carrinho_ativo(user, produto_nome, valor, pagamento_id):
    try:
        if not CANAL_CARRINHOS:
            return None
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
        embed.add_field(name="Horário", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="⏳ Aguardando pagamento...")
        
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
        if not CANAL_PAGOS:
            return
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
        embed.add_field(name="Horário", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        
        if item_entregue:
            embed.add_field(
                name="🔐 Item Entregue",
                value=f"```{item_entregue}```",
                inline=False
            )
        
        embed.set_footer(text="🎉 Produto entregue com sucesso!")
        
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
                        color=0x00ff88,
                        timestamp=datetime.now()
                    )
                    if item_entregue:
                        embed_aprovado.add_field(
                            name="🔐 Item Entregue",
                            value=f"```{item_entregue}```",
                            inline=False
                        )
                    embed_aprovado.set_footer(text="🎉 Entregue com sucesso!")
                    await msg.edit(embed=embed_aprovado)
                except Exception as e:
                    print(f"Erro ao editar mensagem do carrinho: {e}")
                    try:
                        await msg.delete()
                    except:
                        pass
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e:
        print(f"❌ Erro log pagos: {e}")

# ===============================
# DISCORD
# ===============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"✅ Slash commands sincronizados para o servidor {GUILD_ID}")
        else:
            await self.tree.sync()
            print("✅ Slash commands sincronizados globalmente")

    async def on_ready(self):
        print(f"🟢 Logado como {self.user} - NOVA LOJA!")
        
        # Mensagem de boas-vindas no console
        print(f"📊 Estatísticas:")
        print(f"   - Produtos: {len(produtos_disponiveis)}")
        print(f"   - Comandos sincronizados: {len(self.tree.get_commands())}")

bot = Bot()

# ===============================
# CLASSE DO BOTÃO DE COPIAR PIX
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
            
            pix_data = criar_pagamento_pix_com_preco(
                user.id,
                f"{self.produto_id}_{variacao['nome']}",
                variacao["preco"],
                f"{self.produto_nome} - {variacao['nome']}"
            )
            
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento.", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX",
                description=f"**Produto:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto aqui assim que o pagamento for confirmado!")
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
            await interaction.followup.send("📨 Informações enviadas no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao processar variação: {e}")
            try:
                await interaction.followup.send("❌ Ocorreu um erro.", ephemeral=True)
            except:
                pass

# ===============================
# COMANDOS DE ADMIN - ESTOQUE
# ===============================

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
@app_commands.describe(
    produto_id="ID do produto",
    itens="Itens separados por | (ex: conta1:senha1 | conta2:senha2)",
    variacao="Nome da variação (opcional)"
)
async def add_estoque(
    interaction: discord.Interaction,
    produto_id: str,
    itens: str,
    variacao: str = None
):
    try:
        if MEU_ID and interaction.user.id != MEU_ID:
            if CARGO_ADMIN and CARGO_ADMIN not in [role.id for role in interaction.user.roles]:
                await interaction.response.send_message("❌ Apenas o dono ou admin pode usar este comando.", ephemeral=True)
                return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
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
        
        local = f"na variação `{variacao}`" if variacao else "no estoque geral"
        await interaction.response.send_message(f"✅ {len(novos_itens)} itens adicionados {local} para `{produtos_disponiveis[produto_id]['nome']}`!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao adicionar estoque: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="ver_estoque", description="[ADMIN] Ver itens no estoque")
@app_commands.describe(produto_id="ID do produto", variacao="Nome da variação (opcional)")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    try:
        if MEU_ID and interaction.user.id != MEU_ID:
            if CARGO_ADMIN and CARGO_ADMIN not in [role.id for role in interaction.user.roles]:
                await interaction.response.send_message("❌ Apenas o dono ou admin pode usar este comando.", ephemeral=True)
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
            description=descricao[:4000],
            color=0x2b2d31
        )
        embed.set_footer(text=f"Total: {len(itens)} itens | Use /remover_estoque com o índice")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao ver estoque: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

# ===============================
# COMANDOS DE ADMIN - VARIAÇÕES
# ===============================

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
        if MEU_ID and interaction.user.id != MEU_ID:
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
        if MEU_ID and interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        variacoes = produto.get("variacoes", [])
        
        if not variacoes:
            await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nNenhuma variação cadastrada.\n\nUse `/add_variacao` para criar!", ephemeral=True)
            return
        
        descricao = ""
        for i, v in enumerate(variacoes):
            descricao += f"**{i}** - {v['nome']} - R$ {v['preco']:.2f}\n"
        
        embed = discord.Embed(
            title=f"📦 VARIAÇÕES - {produto['nome']}",
            description=descricao,
            color=0x2b2d31
        )
        embed.set_footer(text="Use /editar_variacao ou /remover_variacao com o índice")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao listar variações: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="editar_variacao", description="[ADMIN] Editar nome ou preço de uma variação")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Índice da variação (use /listar_variacoes para ver)",
    novo_nome="Novo nome da variação (opcional)",
    novo_preco="Novo preço da variação (opcional)"
)
async def editar_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int,
    novo_nome: str = None,
    novo_preco: float = None
):
    try:
        if MEU_ID and interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
        if indice < 0 or indice >= len(variacoes):
            await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
            return
        
        variacao = variacoes[indice]
        mensagem = f"✅ Variação editada!\n\n📦 Produto: {produtos_disponiveis[produto_id]['nome']}\n"
        
        if novo_nome:
            mensagem += f"📝 Nome: {variacao['nome']} → {novo_nome}\n"
            variacao["nome"] = novo_nome
        
        if novo_preco:
            mensagem += f"💰 Preço: R$ {variacao['preco']:.2f} → R$ {novo_preco:.2f}\n"
            variacao["preco"] = novo_preco
        
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(mensagem, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao editar variação: {e}")
        await interaction.response.send_message(f"❌ Erro: {e}", ephemeral=True)

@bot.tree.command(name="remover_variacao", description="[ADMIN] Remover variação de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    indice="Número da variação (use /listar_variacoes para ver)"
)
async def remover_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int
):
    try:
        if MEU_ID and interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
        if indice < 0 or indice >= len(variacoes):
            await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
            return
        
        removida
