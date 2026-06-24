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

print("🔧 Iniciando bot G7 STORE...")

# ===============================
# CONFIG - 🔥 TODAS AS VARIÁVEIS DE AMBIENTE
# ===============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN_G7", "")  # ← NUNCA COLOQUE O TOKEN AQUI!
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN_G7", "")  # ← NUNCA COLOQUE O TOKEN AQUI!
WEBHOOK_URL = os.environ.get("WEBHOOK_URL_G7", "https://bot-loja-discord-jty5.onrender.com/webhook")

ARQUIVO_PRODUTO = "produto_g7.txt"
ARQUIVO_PRODUTOS_JSON = "produtos_g7.json"
ARQUIVO_ESTOQUE_JSON = "estoque_g7.json"
ARQUIVO_PAGAMENTOS_PROCESSADOS = "pagamentos_g7.json"

# ===============================
# CONFIGURAÇÕES DO SERVIDOR - 🔥 SEUS IDS
# ===============================
GUILD_ID = 1431125477069688953
CARGO_MEMBRO = 1431125477069688953
CARGO_CLIENTE = 1431125477069688953
CANAL_CARRINHOS = 1513770446158303304
CANAL_PAGOS = 1513770547933089852
MEU_ID = 1431125477069688953
CARGO_ADMIN = 1431125477069688953

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
print(f"📦 Estoque G7 carregado")

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
print(f"📦 {len(produtos_disponiveis)} produtos G7 carregados")

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
            "description": f"Compra G7: {nome_produto}"[:60],
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

        print(f"🔍 Tentando gerar PIX G7 de R$ {preco_formatado} para o produto {produto_id}...")
        
        result = sdk.payment().create(payment_data)
        
        status_code = result.get("status")
        response_data = result.get("response")

        if status_code in [200, 201]:
            payment = response_data
            pix_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
            
            print(f"✅ PIX G7 Gerado com sucesso! ID: {payment.get('id')}")
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
            print(f"❌ ERRO NA API DO MERCADO PAGO G7")
            print(f"Status Code: {status_code}")
            print(f"Resposta: {json.dumps(response_data, indent=2)}")
            print("!"*30 + "\n")
            return None

    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO CÓDIGO DE PAGAMENTO G7: {e}")
        import traceback
        traceback.print_exc()
        return None

# ===============================
# FUNÇÃO PARA ENTREGAR PRODUTO DO ESTOQUE
# ===============================
def entregar_do_estoque(produto_id, variacao_nome=None):
    with estoque_lock:
        if produto_id not in estoque_disponivel:
            print(f"❌ Produto G7 {produto_id} não encontrado no estoque")
            return None
        
        if variacao_nome:
            if variacao_nome in estoque_disponivel[produto_id].get("variacoes", {}):
                itens = estoque_disponivel[produto_id]["variacoes"][variacao_nome]
                if itens and len(itens) > 0:
                    item = itens.pop(0)
                    salvar_estoque(estoque_disponivel)
                    print(f"✅ Entregue G7 da variação {variacao_nome}: {item}")
                    return item
                else:
                    print(f"⚠️ Estoque G7 vazio para variação {variacao_nome}")
                    return None
            else:
                print(f"⚠️ Variação G7 {variacao_nome} não encontrada")
                return None
        
        itens = estoque_disponivel[produto_id].get("itens", [])
        if itens and len(itens) > 0:
            item = itens.pop(0)
            salvar_estoque(estoque_disponivel)
            print(f"✅ Entregue G7 do estoque geral: {item}")
            return item
        
        print(f"⚠️ Estoque G7 vazio para {produto_id}")
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
        canal = bot.get_channel(CANAL_CARRINHOS)
        if not canal:
            return None
        
        embed = discord.Embed(
            title="🛒 NOVO CARRINHO ATIVO G7",
            color=0xffaa00,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Cliente", value=user.mention, inline=True)
        embed.add_field(name="Produto", value=produto_nome, inline=True)
        embed.add_field(name="Valor", value=f"R$ {valor:.2f}", inline=True)
        embed.add_field(name="Horário", value=datetime.now().strftime("%d/%m/%Y %H:%M:%S"), inline=False)
        embed.add_field(name="Pagamento", value=f"`{pagamento_id}`", inline=False)
        embed.set_footer(text="⏳ Aguardando pagamento G7...")
        
        mensagem = await canal.send(embed=embed)
        
        carrinhos_ativos[str(pagamento_id)] = {
            "canal": canal.id,
            "mensagem_id": mensagem.id,
            "usuario": user.id,
            "produto": produto_nome
        }
        
        return mensagem
    except Exception as e:
        print(f"❌ Erro log carrinho G7: {e}")
        return None

async def log_pagamento_confirmado(user, produto_nome, valor, pagamento_id, item_entregue=None):
    try:
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if not canal_pagos:
            return
        
        embed = discord.Embed(
            title="✅ PAGAMENTO CONFIRMADO G7",
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
                name="🔐 Item Entregue G7",
                value=f"```{item_entregue}```",
                inline=False
            )
        
        embed.set_footer(text="🎉 Produto G7 entregue com sucesso!")
        
        await canal_pagos.send(embed=embed)
        
        if str(pagamento_id) in carrinhos_ativos:
            dados = carrinhos_ativos[str(pagamento_id)]
            canal_carrinho = bot.get_channel(dados["canal"])
            if canal_carrinho:
                try:
                    msg = await canal_carrinho.fetch_message(dados["mensagem_id"])
                    embed_aprovado = discord.Embed(
                        title="✅ PAGAMENTO APROVADO G7",
                        description=f"Cliente: {user.mention}\nProduto G7: {produto_nome}\nValor: R$ {valor:.2f}",
                        color=0x00ff88,
                        timestamp=datetime.now()
                    )
                    if item_entregue:
                        embed_aprovado.add_field(
                            name="🔐 Item Entregue G7",
                            value=f"```{item_entregue}```",
                            inline=False
                        )
                    embed_aprovado.set_footer(text="🎉 Entregue G7 com sucesso!")
                    await msg.edit(embed=embed_aprovado)
                except Exception as e:
                    print(f"Erro ao editar mensagem do carrinho G7: {e}")
                    try:
                        await msg.delete()
                    except:
                        pass
            del carrinhos_ativos[str(pagamento_id)]
    except Exception as e:
        print(f"❌ Erro log pagos G7: {e}")

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
        await self.tree.sync()
        print("✅ Slash commands G7 sincronizados")

    async def on_ready(self):
        print(f"🟢 G7 Logado como {self.user}")

bot = Bot()

# ===============================
# CLASSE DO BOTÃO DE COPIAR PIX
# ===============================
class CopiarPIXView(discord.ui.View):
    def __init__(self, codigo_pix: str):
        super().__init__(timeout=300)
        self.codigo_pix = codigo_pix

    @discord.ui.button(label="📋 Copiar código PIX G7", style=discord.ButtonStyle.primary)
    async def copiar_pix(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_message(
                f"```{self.codigo_pix}```", 
                ephemeral=True
            )
        except Exception as e:
            print(f"❌ Erro ao copiar PIX G7: {e}")
            await interaction.response.send_message("❌ Erro ao copiar PIX G7", ephemeral=True)

# ===============================
# MODAL PARA 2FA
# ===============================
class Modal2FA(discord.ui.Modal, title="Gerar Código 2FA G7"):
    chave = discord.ui.TextInput(
        label="Chave 2FA G7",
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
                title="🔐 **CÓDIGO 2FA GERADO G7**",
                description="Use o código abaixo para acessar sua conta G7:",
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
                @discord.ui.button(label="📋 Copiar Código G7", style=discord.ButtonStyle.success)
                async def copiar(self, i: discord.Interaction, b: discord.ui.Button):
                    try:
                        await i.response.send_message(f"{self.codigo}", ephemeral=True)
                    except Exception as e:
                        print(f"❌ Erro ao copiar G7: {e}")

            await interaction.followup.send(embed=embed, view=CopiarCodigoView(codigo_atual), ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao gerar código 2FA G7: {e}")
            try:
                await interaction.followup.send(f"❌ Erro ao gerar código G7: {e}", ephemeral=True)
            except:
                pass

# ===============================
# VIEW PARA O CANAL 2FA
# ===============================
class Canal2FAView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔐 Gerar Código 2FA G7", style=discord.ButtonStyle.success, custom_id="btn_gerar_2fa_g7")
    async def gerar_2fa_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(Modal2FA())
        except Exception as e:
            print(f"❌ Erro ao abrir modal 2FA G7: {e}")
            await interaction.response.send_message("❌ Erro ao abrir modal G7", ephemeral=True)

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
            placeholder="Escolha uma opção G7...",
            options=options,
            custom_id="select_variacao_g7"
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
                    f"❌ **{variacao['nome']} está esgotado!** Aguarde reposição G7.",
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
                await interaction.followup.send("❌ Erro ao gerar pagamento G7.", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX G7",
                description=f"**Produto G7:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto G7 aqui assim que o pagamento for confirmado!")
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
            await interaction.followup.send("📨 Informações G7 enviadas no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao processar variação G7: {e}")
            try:
                await interaction.followup.send("❌ Ocorreu um erro G7.", ephemeral=True)
            except:
                pass

# ===============================
# COMANDOS DE ADMIN - ESTOQUE
# ===============================
@bot.tree.command(name="add_estoque_g7", description="[ADMIN] Adicionar itens ao estoque G7")
@app_commands.describe(
    produto_id="ID do produto G7",
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
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
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
        
        local = f"na variação `{variacao}`" if variacao else "no estoque G7 geral"
        await interaction.response.send_message(f"✅ {len(novos_itens)} itens G7 adicionados {local} para `{produtos_disponiveis[produto_id]['nome']}`!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao adicionar estoque G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="ver_estoque_g7", description="[ADMIN] Ver itens no estoque G7")
@app_commands.describe(produto_id="ID do produto G7", variacao="Nome da variação (opcional)")
async def ver_estoque(interaction: discord.Interaction, produto_id: str, variacao: str = None):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        
        if variacao:
            itens = estoque_disponivel.get(produto_id, {}).get("variacoes", {}).get(variacao, [])
        else:
            itens = estoque_disponivel.get(produto_id, {}).get("itens", [])
        
        if not itens:
            await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nEstoque G7 vazio!", ephemeral=True)
            return
        
        descricao = ""
        for i, item in enumerate(itens):
            descricao += f"**{i}** - `{item}`\n"
        
        embed = discord.Embed(
            title=f"📦 ESTOQUE G7 - {produto['nome']}",
            description=descricao,
            color=0x2b2d31
        )
        embed.set_footer(text=f"Total: {len(itens)} itens G7 | Use /remover_estoque_g7 com o índice")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao ver estoque G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

# ===============================
# COMANDOS DE ADMIN - VARIAÇÕES
# ===============================
@bot.tree.command(name="add_variacao_g7", description="[ADMIN] Adicionar variação a um produto G7")
@app_commands.describe(
    produto_id="ID do produto G7",
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
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        if "variacoes" not in produtos_disponiveis[produto_id]:
            produtos_disponiveis[produto_id]["variacoes"] = []
        
        produtos_disponiveis[produto_id]["variacoes"].append({
            "nome": nome,
            "preco": preco
        })
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Variação G7 adicionada!\n\n"
            f"📦 Produto G7: {produtos_disponiveis[produto_id]['nome']}\n"
            f"🎮 Opção: {nome}\n"
            f"💰 Preço: R$ {preco:.2f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao adicionar variação G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="listar_variacoes_g7", description="[ADMIN] Listar variações de um produto G7")
@app_commands.describe(produto_id="ID do produto G7")
async def listar_variacoes(interaction: discord.Interaction, produto_id: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        variacoes = produto.get("variacoes", [])
        
        if not variacoes:
            await interaction.response.send_message(f"📦 **{produto['nome']}**\n\nNenhuma variação G7 cadastrada.\n\nUse `/add_variacao_g7` para criar!", ephemeral=True)
            return
        
        descricao = ""
        for i, v in enumerate(variacoes):
            descricao += f"**{i}** - {v['nome']} - R$ {v['preco']:.2f}\n"
        
        embed = discord.Embed(
            title=f"📦 VARIAÇÕES G7 - {produto['nome']}",
            description=descricao,
            color=0x2b2d31
        )
        embed.set_footer(text="Use /editar_variacao_g7 ou /remover_variacao_g7 com o índice")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao listar variações G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="editar_variacao_g7", description="[ADMIN] Editar nome ou preço de uma variação G7")
@app_commands.describe(
    produto_id="ID do produto G7",
    indice="Índice da variação (use /listar_variacoes_g7 para ver)",
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
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
        if indice < 0 or indice >= len(variacoes):
            await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
            return
        
        variacao = variacoes[indice]
        mensagem = f"✅ Variação G7 editada!\n\n📦 Produto G7: {produtos_disponiveis[produto_id]['nome']}\n"
        
        if novo_nome:
            mensagem += f"📝 Nome: {variacao['nome']} → {novo_nome}\n"
            variacao["nome"] = novo_nome
        
        if novo_preco:
            mensagem += f"💰 Preço: R$ {variacao['preco']:.2f} → R$ {novo_preco:.2f}\n"
            variacao["preco"] = novo_preco
        
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(mensagem, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao editar variação G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="remover_variacao_g7", description="[ADMIN] Remover variação de um produto G7")
@app_commands.describe(
    produto_id="ID do produto G7",
    indice="Número da variação (use /listar_variacoes_g7 para ver)"
)
async def remover_variacao(
    interaction: discord.Interaction,
    produto_id: str,
    indice: int
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        variacoes = produtos_disponiveis[produto_id].get("variacoes", [])
        if indice < 0 or indice >= len(variacoes):
            await interaction.response.send_message(f"❌ Índice inválido! Use 0 a {len(variacoes)-1}", ephemeral=True)
            return
        
        removida = variacoes.pop(indice)
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Variação G7 removida!\n\n"
            f"📦 Produto G7: {produtos_disponiveis[produto_id]['nome']}\n"
            f"🎮 Opção removida: {removida['nome']}\n"
            f"💰 Preço: R$ {removida['preco']:.2f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao remover variação G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

# ===============================
# COMANDOS DE CLIENTE
# ===============================
@bot.tree.command(name="produtos_g7", description="Ver todos os produtos G7 disponíveis")
async def listar_produtos(interaction: discord.Interaction):
    try:
        if not produtos_disponiveis:
            await interaction.response.send_message("📦 **Nenhum produto G7 cadastrado ainda!**\n\nUse `/criar_produto_g7` para adicionar.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🛒 G7 STORE - PRODUTOS",
            description="Use `/comprar_g7 [id]` para adquirir qualquer produto G7!",
            color=0x2b2d31,
            timestamp=datetime.now()
        )
        
        for key, prod in produtos_disponiveis.items():
            tipo_texto = "Automática" if prod.get('tipo') == 'auto' else "Manual"
            qtd_variacoes = len(prod.get("variacoes", []))
            qtd_estoque = verificar_estoque(key)
            estoque_texto = f" | {qtd_estoque} em estoque G7" if prod.get('tipo') == 'auto' else ""
            variacoes_texto = f" | {qtd_variacoes} opções" if qtd_variacoes > 0 else ""
            
            embed.add_field(
                name=f"📦 {prod['nome']}",
                value=f"💰 Preço: R$ {prod['preco']:.2f}\n"
                      f"📝 Entrega: {tipo_texto}{variacoes_texto}{estoque_texto}\n"
                      f"🆔 ID: `{key}`",
                inline=False
            )
        
        embed.set_footer(text="G7 STORE")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao listar produtos G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

# ===============================
# DESIGN DE PRODUTO - ESTILO G7 STORE
# ===============================
async def criar_embed_produto_tzada(produto_id: str, produto_info: dict):
    """Cria um único embed estilo G7 Store com imagem no topo e texto embaixo"""
    try:
        imagem_url = produto_info.get('imagem', '')
        qtd_variacoes = len(produto_info.get("variacoes", []))
        qtd_estoque = verificar_estoque(produto_id)
        tipo_entrega = "🤖 Entrega Automática G7!" if produto_info.get('tipo') == 'auto' else "👨‍💼 Entrega Manual G7"
        
        descricao = produto_info.get('descricao', 'Sem descrição')
        
        if '|' in descricao:
            beneficios = [b.strip() for b in descricao.split('|')]
            descricao_formatada = "\n".join([f"✅ {b}" for b in beneficios if b])
        else:
            descricao_formatada = f"✅ {descricao}"
        
        estoque_info = ""
        if produto_info.get('tipo') == 'auto':
            estoque_info = f"\n📦 Estoque G7: {qtd_estoque} unidades"
        
        embed = discord.Embed(
            color=0xffa500
        )
        
        if imagem_url and imagem_url != "":
            embed.set_image(url=imagem_url)
        
        embed.title = f"⚡ {tipo_entrega}"
        embed.description = f"**{produto_info['nome']}**\n\n{descricao_formatada}{estoque_info}"
        
        embed.add_field(
            name="💰 Valor à vista G7",
            value=f"R$ {produto_info['preco']:.2f}",
            inline=True
        )
        
        if produto_info.get('tipo') == 'auto':
            embed.add_field(
                name="📦 Restam G7",
                value=f"{qtd_estoque}",
                inline=True
            )
        
        if qtd_variacoes > 0:
            embed.add_field(
                name="🎮 Opções Disponíveis G7",
                value=f"{qtd_variacoes} variações",
                inline=True
            )
        
        embed.set_footer(text="G7 STORE - Clique no botão abaixo para comprar!")
        embed.timestamp = datetime.now()
        
        return embed
    except Exception as e:
        print(f"❌ Erro ao criar embed G7: {e}")
        return None

class ProdutoCompraView(discord.ui.View):
    def __init__(self, produto_id: str, produto_nome: str, variacoes: list = None):
        super().__init__(timeout=None)
        self.produto_id = produto_id
        self.produto_nome = produto_nome
        self.variacoes = variacoes or []
    
    @discord.ui.button(label="🛒 Comprar G7", style=discord.ButtonStyle.success, custom_id="btn_comprar_g7")
    async def comprar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            if self.variacoes and len(self.variacoes) > 0:
                view = VariacoesView(self.produto_id, self.produto_nome, self.variacoes)
                await interaction.followup.send(
                    f"📦 **{self.produto_nome}**\n\nSelecione a opção G7 desejada:",
                    view=view,
                    ephemeral=True
                )
                return
            
            user = interaction.user
            
            produto_info = produtos_disponiveis[self.produto_id]
            
            qtd_estoque = verificar_estoque(self.produto_id)
            if qtd_estoque == 0 and produto_info.get("tipo") == "auto":
                await interaction.followup.send("❌ **Produto G7 esgotado!** Aguarde reposição.", ephemeral=True)
                return
            
            pix_data = criar_pagamento_pix_com_preco(user.id, self.produto_id, produto_info["preco"], self.produto_nome)
            
            if not pix_data:
                await interaction.followup.send("❌ Erro ao gerar pagamento G7.", ephemeral=True)
                return
            
            await log_carrinho_ativo(
                user=user,
                produto_nome=pix_data['produto'],
                valor=pix_data['preco'],
                pagamento_id=pix_data.get('payment_id', 'N/A')
            )
            
            embed_pix = discord.Embed(
                title="🧾 PAGAMENTO PIX G7",
                description=f"**Produto G7:** {pix_data['produto']}\n**Valor:** R$ {pix_data['preco']:.2f}",
                color=0x00ff88
            )
            
            try:
                expiracao = datetime.fromisoformat(pix_data["expiration"].replace("Z", "+00:00"))
                tempo_restante = expiracao - datetime.now(expiracao.tzinfo)
                minutos = int(tempo_restante.total_seconds() / 60)
                embed_pix.add_field(name="⏰ Expira em", value=f"{minutos} minutos", inline=True)
            except:
                embed_pix.add_field(name="⏰ Expira em", value="15 minutos", inline=True)
            
            embed_pix.set_footer(text="Você receberá o produto G7 aqui assim que o pagamento for confirmado!")
            
            qr_image_data = base64.b64decode(pix_data["qr_code_base64"])
            copiar_view = CopiarPIXView(pix_data["qr_code"])
            
            with BytesIO(qr_image_data) as image_binary:
                image_binary.seek(0)
                file = discord.File(fp=image_binary, filename="qrcode.png")
                await user.send(embed=embed_pix, file=file, view=copiar_view)
                
            await interaction.followup.send("📨 Informações G7 enviadas no seu privado!", ephemeral=True)
        except Exception as e:
            print(f"❌ Erro ao processar compra G7: {e}")
            try:
                await interaction.followup.send("❌ Ocorreu um erro G7.", ephemeral=True)
            except:
                pass

# ===============================
# COMANDOS DE ADMIN - CONFIGURAÇÃO
# ===============================
@bot.tree.command(name="configurar_produto_g7", description="[ADMIN] Criar/atualizar canal de um produto G7")
@app_commands.describe(
    produto_id="ID do produto G7",
    nome_canal="Nome do canal"
)
async def configurar_produto(
    interaction: discord.Interaction,
    produto_id: str,
    nome_canal: str
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto_info = produtos_disponiveis[produto_id]
        guild = interaction.guild
        
        canal = discord.utils.get(guild.channels, name=nome_canal)
        if not canal:
            canal = await guild.create_text_channel(nome_canal)
        
        embed = await criar_embed_produto_tzada(produto_id, produto_info)
        if not embed:
            await interaction.followup.send("❌ Erro ao criar embed do produto G7.", ephemeral=True)
            return
            
        view = ProdutoCompraView(produto_id, produto_info['nome'], produto_info.get('variacoes', []))
        
        await canal.purge(limit=10)
        await canal.send(embed=embed, view=view)
        
        await interaction.followup.send(f"✅ Canal {canal.mention} configurado para o produto G7 `{produto_info['nome']}`!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao configurar produto G7: {e}")
        try:
            await interaction.followup.send(f"❌ Erro G7: {e}", ephemeral=True)
        except:
            pass

@bot.tree.command(name="remover_estoque_g7", description="🗑️ Remove itens específicos do estoque G7")
@app_commands.describe(
    produto_id="ID do produto G7",
    indice="Número do item a remover (veja com /ver_estoque_g7)",
    variacao="Nome da variação (deixe em branco para produto sem variações)"
)
async def remover_estoque(interaction: discord.Interaction, produto_id: str, indice: int, variacao: str = None):
    await interaction.response.defer(ephemeral=True)
    
    try:
        if interaction.user.id != MEU_ID and CARGO_ADMIN not in [role.id for role in interaction.user.roles]:
            await interaction.followup.send("❌ Apenas administradores G7 podem remover estoque!", ephemeral=True)
            return
        
        if produto_id not in estoque_disponivel:
            await interaction.followup.send(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        with estoque_lock:
            produto_estoque = estoque_disponivel[produto_id]
            
            if variacao:
                if variacao not in produto_estoque.get("variacoes", {}):
                    await interaction.followup.send(f"❌ Variação G7 `{variacao}` não encontrada para o produto `{produto_id}`!", ephemeral=True)
                    return
                
                lista_itens = produto_estoque["variacoes"][variacao]
                
                if indice < 0 or indice >= len(lista_itens):
                    await interaction.followup.send(
                        f"❌ Índice `{indice}` inválido! O estoque G7 tem apenas **{len(lista_itens)}** itens (0 a {len(lista_itens)-1}).",
                        ephemeral=True
                    )
                    return
                
                item_removido = lista_itens.pop(indice)
                salvar_estoque(estoque_disponivel)
                
                await interaction.followup.send(
                    f"✅ Item G7 **#{indice}** removido da variação `{variacao}` do produto `{produto_id}`!\n"
                    f"🗑️ Item removido: `{item_removido}`\n"
                    f"📦 Estoque G7 restante: **{len(lista_itens)}** itens",
                    ephemeral=True
                )
            else:
                lista_itens = produto_estoque.get("itens", [])
                
                if indice < 0 or indice >= len(lista_itens):
                    await interaction.followup.send(
                        f"❌ Índice `{indice}` inválido! O estoque G7 tem apenas **{len(lista_itens)}** itens (0 a {len(lista_itens)-1}).",
                        ephemeral=True
                    )
                    return
                
                item_removido = lista_itens.pop(indice)
                salvar_estoque(estoque_disponivel)
                
                await interaction.followup.send(
                    f"✅ Item G7 **#{indice}** removido do produto `{produto_id}`!\n"
                    f"🗑️ Item removido: `{item_removido}`\n"
                    f"📦 Estoque G7 restante: **{len(lista_itens)}** itens",
                    ephemeral=True
                )
    except Exception as e:
        print(f"❌ Erro ao remover estoque G7: {e}")
        await interaction.followup.send(f"❌ Erro ao remover estoque G7: {e}", ephemeral=True)

@bot.tree.command(name="sincronizar_canal_g7", description="[ADMIN] Atualizar embed de um canal G7 existente")
@app_commands.describe(produto_id="ID do produto G7")
async def sincronizar_canal(interaction: discord.Interaction, produto_id: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produto_info = produtos_disponiveis[produto_id]
        canal = interaction.channel
        
        embed = await criar_embed_produto_tzada(produto_id, produto_info)
        if not embed:
            await interaction.followup.send("❌ Erro ao criar embed do produto G7.", ephemeral=True)
            return
            
        view = ProdutoCompraView(produto_id, produto_info['nome'], produto_info.get('variacoes', []))
        
        await canal.purge(limit=5)
        await canal.send(embed=embed, view=view)
        
        await interaction.followup.send(f"✅ Canal G7 sincronizado!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao sincronizar canal G7: {e}")
        try:
            await interaction.followup.send(f"❌ Erro G7: {e}", ephemeral=True)
        except:
            pass

@bot.tree.command(name="configurar_2fa_g7", description="[ADMIN] Configurar canal de 2FA G7 com botão")
async def configurar_2fa(interaction: discord.Interaction):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🔐 GERADOR DE CÓDIGO 2FA G7",
            description="Clique no botão abaixo para gerar seu código 2FA G7 de forma rápida e segura.\n\n"
                        "1️⃣ Clique em **Gerar Código 2FA G7**\n"
                        "2️⃣ Cole sua chave secreta\n"
                        "3️⃣ O bot enviará o código atual para você!",
            color=0x00ff88
        )
        embed.set_footer(text="G7 STORE - Segurança em primeiro lugar")
        
        await interaction.channel.send(embed=embed, view=Canal2FAView())
        await interaction.response.send_message("✅ Canal de 2FA G7 configurado!", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao configurar 2FA G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="set_imagem_g7", description="[ADMIN] Definir imagem de um produto G7")
@app_commands.describe(produto_id="ID do produto G7", url_imagem="URL da imagem")
async def set_imagem(interaction: discord.Interaction, produto_id: str, url_imagem: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 `{produto_id}` não encontrado!", ephemeral=True)
            return
        
        produtos_disponiveis[produto_id]["imagem"] = url_imagem
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Imagem G7 atualizada!\n🖼️ Nova imagem: {url_imagem}\n\n💡 Use `/sincronizar_canal_g7 {produto_id}` para aplicar.",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao definir imagem G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

# ===============================
# COMANDOS DE ADMIN (GERENCIAMENTO BASE)
# ===============================
@bot.tree.command(name="criar_produto_g7", description="[ADMIN] Criar um novo produto G7")
@app_commands.describe(
    id="ID único do produto G7",
    nome="Nome do produto G7",
    preco="Preço em R$",
    descricao="Descrição do produto G7 (use | para separar benefícios)",
    tipo="Tipo: auto ou manual"
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
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if id in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 com ID `{id}` já existe!", ephemeral=True)
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
        
        tipo_texto = "🤖 Entrega automática G7" if tipo == "auto" else "👨‍💼 Entrega manual G7"
        
        await interaction.response.send_message(
            f"✅ Produto G7 criado!\n\n📦 ID: `{id}`\n📝 Nome: {nome}\n💰 Preço: R$ {preco:.2f}\n🎮 Tipo: {tipo_texto}\n\n💡 Use `/add_estoque_g7` para adicionar itens!\n💡 Use `/add_variacao_g7` para adicionar opções!\n💡 Use `/configurar_produto_g7 {id} {id}` para criar o canal!",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao criar produto G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="editar_preco_g7", description="[ADMIN] Alterar preço de um produto G7")
@app_commands.describe(
    produto_id="ID do produto G7",
    novo_preco="Novo preço em R$"
)
async def editar_preco(interaction: discord.Interaction, produto_id: str, novo_preco: float):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        preco_antigo = produto["preco"]
        produto["preco"] = novo_preco
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(
            f"✅ Preço G7 atualizado!\n📦 Produto G7: {produto['nome']}\n📉 Antigo: R$ {preco_antigo:.2f}\n📈 Novo: R$ {novo_preco:.2f}",
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao editar preço G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="editar_produto_g7", description="[ADMIN] Alterar nome/descrição G7")
@app_commands.describe(
    produto_id="ID do produto G7",
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
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis[produto_id]
        mensagem = f"✅ Produto G7 atualizado!\n\n📦 ID: `{produto_id}`\n"
        
        if novo_nome:
            mensagem += f"📝 Nome: {produto['nome']} → {novo_nome}\n"
            produto["nome"] = novo_nome
        
        if nova_descricao:
            mensagem += f"📄 Descrição G7 atualizada\n"
            produto["descricao"] = nova_descricao
        
        salvar_produtos(produtos_disponiveis)
        await interaction.response.send_message(mensagem, ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao editar produto G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="remover_produto_g7", description="[ADMIN] Remover um produto G7")
@app_commands.describe(produto_id="ID do produto G7")
async def remover_produto(interaction: discord.Interaction, produto_id: str):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.response.send_message(f"❌ Produto G7 não encontrado!", ephemeral=True)
            return
        
        produto = produtos_disponiveis.pop(produto_id)
        salvar_produtos(produtos_disponiveis)
        
        await interaction.response.send_message(f"✅ Produto G7 removido!\n📦 Removido: {produto['nome']}", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao remover produto G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

@bot.tree.command(name="entregar_g7", description="[ADMIN] Entregar produto manual do estoque G7")
@app_commands.describe(
    usuario="ID do usuário",
    produto_id="ID do produto G7",
    indice="Índice do item no estoque (opcional, use /ver_estoque_g7 para ver)"
)
async def entregar_produto(
    interaction: discord.Interaction, 
    usuario: str, 
    produto_id: str,
    indice: int = -1
):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        user_id = int(usuario)
        user = await bot.fetch_user(user_id)
        
        if not user:
            await interaction.followup.send("❌ Usuário G7 não encontrado.", ephemeral=True)
            return
        
        if produto_id not in produtos_disponiveis:
            await interaction.followup.send(f"❌ Produto G7 não encontrado!", ephemeral=True)
            return
        
        with estoque_lock:
            if produto_id not in estoque_disponivel:
                estoque_disponivel[produto_id] = {"itens": [], "variacoes": {}}
            
            itens = estoque_disponivel[produto_id].get("itens", [])
            
            if not itens:
                await interaction.followup.send(f"❌ **Estoque G7 vazio para {produtos_disponiveis[produto_id]['nome']}!**\n\nUse `/add_estoque_g7` para adicionar itens.", ephemeral=True)
                return
            
            if indice == -1:
                item = itens.pop(0)
            else:
                if indice < 0 or indice >= len(itens):
                    await interaction.followup.send(f"❌ Índice inválido! Use 0 a {len(itens)-1} ou /ver_estoque_g7 para ver os índices.", ephemeral=True)
                    return
                item = itens.pop(indice)
            
            salvar_estoque(estoque_disponivel)
        
        produto = produtos_disponiveis[produto_id]
        
        await user.send(
            f"🎮 **Sua {produto['nome']} G7 chegou!**\n\n"
            f"```{item}```\n\n"
            "✅ Obrigado pela preferência G7!"
        )
        
        await interaction.followup.send(f"✅ **{produto['nome']} G7 entregue para {user.name}!**\n🔐 Item: `{item}`\n📊 Restam {len(estoque_disponivel[produto_id].get('itens', []))} itens em estoque G7.", ephemeral=True)
        
        canal_pagos = bot.get_channel(CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="📦 PRODUTO G7 ENTREGUE",
                color=0x3498db,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Cliente", value=user.mention, inline=True)
            embed.add_field(name="📦 Produto G7", value=produto['nome'], inline=True)
            embed.add_field(name="🔐 Item", value=f"`{item}`", inline=False)
            embed.set_footer(text=f"Entregue G7 por: {interaction.user.name}")
            await canal_pagos.send(embed=embed)
    except ValueError:
        await interaction.followup.send("❌ ID inválido G7.", ephemeral=True)
    except Exception as e:
        print(f"❌ Erro ao entregar G7: {e}")
        try:
            await interaction.followup.send(f"❌ Erro G7: {e}", ephemeral=True)
        except:
            pass

@bot.tree.command(name="backup_g7", description="[ADMIN] Fazer backup dos produtos G7")
async def fazer_backup(interaction: discord.Interaction):
    try:
        if interaction.user.id != MEU_ID:
            await interaction.response.send_message("❌ Apenas o dono G7 pode usar este comando.", ephemeral=True)
            return
        
        backup_data = json.dumps(produtos_disponiveis, indent=2, ensure_ascii=False)
        import io
        file = discord.File(io.StringIO(backup_data), filename="backup_produtos_g7.json")
        
        await interaction.response.send_message(
            "✅ Backup G7 realizado! Guarde este arquivo.",
            file=file,
            ephemeral=True
        )
    except Exception as e:
        print(f"❌ Erro ao fazer backup G7: {e}")
        await interaction.response.send_message(f"❌ Erro G7: {e}", ephemeral=True)

# ===============================
# COMANDO 2FA
# ===============================
@bot.tree.command(name="2fa_g7", description="Gerar código 2FA G7 a partir da chave")
@app_commands.describe(chave="Sua chave 2FA G7 (ex: 7J64V3P3E77J3LKNUGSZ5QANTLRLTKVL)")
async def gerar_2fa(interaction: discord.Interaction, chave: str):
    try:
        await interaction.response.defer(ephemeral=True)
        
        chave = chave.strip().upper()
        if len(chave) < 16:
            embed = discord.Embed(
                title="❌ **CHAVE 2FA G7 INVÁLIDA**",
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
            title="🔐 **CÓDIGO 2FA G7 GERADO**",
            description="Use o código abaixo para acessar sua conta G7:",
            color=0x00ff88,
            timestamp=datetime.now()
        )
        embed.add_field(name="📋 **CÓDIGO:**", value=f"```{codigo_atual}```", inline=False)
        embed.add_field(name="⏰ **VÁLIDO POR:**", value=f"{tempo_restante} segundos", inline=True)
        embed.add_field(name="🔑 **SUA CHAVE:**", value=f"||{chave}||", inline=False)
        embed.set_footer(text="O código expira em 30 segundos.")
        
        class CopiarCodigoView(discord.ui.View):
            def __init__(self, codigo: str):
                super().__init__(timeout=60)
                self.codigo = codigo
            @discord.ui.button(label="📋 Copiar Código G7", style=discord.ButtonStyle.success)
            async def copiar(self, i: discord.Interaction, b: discord.ui.Button):
                try:
                    await i.response.send_message(f"{self.codigo}", ephemeral=True)
                except Exception as e:
                    print(f"❌ Erro ao copiar G7: {e}")

        await interaction.followup.send(embed=embed, view=CopiarCodigoView(codigo_atual), ephemeral=True)
    except Exception as e:
        print(f"❌ Erro 2FA G7: {e}")
        try:
            await interaction.followup.send("❌ Erro ao gerar código G7. Verifique a chave.", ephemeral=True)
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
    start_time = time.time()
    print("\n" + "⚡" * 20)
    print(f"WEBHOOK G7 RECEBIDO ÀS {datetime.now().strftime('%H:%M:%S')}")
    
    data = {}
    if request.is_json:
        data = request.json
    elif request.form:
        data = request.form.to_dict()
    
    print(f"📩 Dados G7 recebidos: {json.dumps(data, indent=2)}")
    
    payment_id = None
    
    if isinstance(data, dict):
        payment_id = data.get('data', {}).get('id')
        
        if not payment_id:
            payment_id = data.get('id')
            
        if not payment_id and 'resource' in data:
            resource = data.get('resource', '')
            payment_id = resource.split('/')[-1] if '/' in resource else None

    if not payment_id:
        payment_id = request.args.get('id') or request.args.get('data.id')
    
    if not payment_id:
        print("⚠️ Webhook G7 recebido, mas nenhum ID de pagamento encontrado.")
        return "OK", 200
    
    print(f"💰 ID de Pagamento G7 Identificado: {payment_id}")

    with webhook_lock:
        if str(payment_id) in pagamentos_processados:
            print(f"⚠️ Pagamento G7 {payment_id} já foi processado! Ignorando...")
            return "OK", 200
        
        try:
            print(f"🔍 Buscando pagamento G7 {payment_id} no Mercado Pago...")
            payment_response = sdk.payment().get(payment_id)
            print(f"📦 Resposta do MP G7: status={payment_response.get('status')}")
            
            if payment_response["status"] == 200:
                payment = payment_response["response"]
                print(f"✅ Status do pagamento G7: {payment.get('status')}")
                
                if payment["status"] == "approved":
                    print("🎉 PAGAMENTO G7 APROVADO!")
                    
                    pagamentos_processados.add(str(payment_id))
                    salvar_pagamentos_processados(pagamentos_processados)
                    print(f"✅ Pagamento G7 {payment_id} marcado como processado")
                    
                    ref = payment.get("external_reference", "")
                    print(f"🔗 External reference G7: {ref}")
                    
                    if ref:
                        partes = ref.split('_')
                        print(f"🧩 Partes da referência G7: {partes}")
                        
                        if len(partes) >= 3:
                            user_id = int(partes[-2])
                            timestamp = partes[-1]
                            
                            if len(partes) == 3:
                                produto_id = partes[0]
                                variacao_nome = None
                            else:
                                produto_id = partes[0]
                                variacao_nome = partes[1]
                            
                            print(f"📦 Produto G7 ID: {produto_id}")
                            print(f"👤 User ID G7: {user_id}")
                            
                            user = bot.get_user(user_id)
                            if not user:
                                try:
                                    future = asyncio.run_coroutine_threadsafe(
                                        bot.fetch_user(user_id), bot.loop
                                    )
                                    user = future.result(timeout=10)
                                    print(f"👤 Usuário G7 encontrado: {user}")
                                except Exception as e:
                                    print(f"❌ Erro ao buscar usuário G7: {e}")
                            
                            if user and produto_id in produtos_disponiveis:
                                produto_info = produtos_disponiveis[produto_id]
                                print(f"📦 Produto G7: {produto_info['nome']} - Tipo: {produto_info.get('tipo')}")
                                
                                if produto_info.get("tipo") == "auto":
                                    item = entregar_do_estoque(produto_id, variacao_nome=variacao_nome)
                                    
                                    if item:
                                        async def enviar_dm():
                                            try:
                                                await user.send(
                                                    f"✅ **Pagamento G7 confirmado!**\n\n"
                                                    f"📦 **{produto_info['nome']}**\n\n"
                                                    f"🔐 **Seu produto G7:**\n```{item}```\n\n"
                                                    "✅ Obrigado pela preferência G7!"
                                                )
                                                process_time = time.time() - start_time
                                                print(f"🚀 ENTREGA G7 REALIZADA EM {process_time:.2f} SEGUNDOS!")
                                                
                                                await log_pagamento_confirmado(
                                                    user=user,
                                                    produto_nome=produto_info['nome'],
                                                    valor=payment.get('transaction_amount', 0),
                                                    pagamento_id=payment_id,
                                                    item_entregue=item
                                                )
                                            except discord.Forbidden:
                                                print(f"⚠️ DM fechada para {user.name} G7. Avisando no canal...")
                                                canal_pagos = bot.get_channel(CANAL_PAGOS)
                                                if canal_pagos:
                                                    await canal_pagos.send(f"⚠️ {user.mention}, seu pagamento G7 de **{produto_info['nome']}** foi aprovado, mas sua DM está fechada! Abra um ticket para receber seu produto G7.")
                                                with estoque_lock:
                                                    if variacao_nome:
                                                        estoque_disponivel[produto_id]["variacoes"][variacao_nome].insert(0, item)
                                                    else:
                                                        estoque_disponivel[produto_id]["itens"].insert(0, item)
                                                    salvar_estoque(estoque_disponivel)
                                        asyncio.run_coroutine_threadsafe(enviar_dm(), bot.loop)
                                    else:
                                        async def avisar_esgotado():
                                            try:
                                                await user.send(f"✅ **Pagamento G7 confirmado!**\n\n📦 **{produto_info['nome']}**\n\n⚠️ **Estoque G7 esgotado!** Um administrador irá entregar em breve.")
                                            except:
                                                pass
                                        asyncio.run_coroutine_threadsafe(avisar_esgotado(), bot.loop)
                                else:
                                    async def avisar_manual():
                                        try:
                                            await user.send(f"✅ **Pagamento G7 confirmado!**\n\n📦 **{produto_info['nome']}**\n\n⏳ Um administrador G7 irá entregar seu produto em breve!")
                                        except:
                                            pass
                                    asyncio.run_coroutine_threadsafe(avisar_manual(), bot.loop)
                            else:
                                print(f"⚠️ Usuário G7 ou produto não encontrado")
                        else:
                            print(f"⚠️ Referência G7 inválida: {ref}")
                    else:
                        print(f"⚠️ Nenhuma referência externa G7 encontrada")
                else:
                    print(f"⚠️ Pagamento G7 não aprovado. Status: {payment['status']}")
            else:
                print(f"❌ Erro ao buscar pagamento G7: {payment_response}")
        except Exception as e:
            print(f"❌ ERRO NO WEBHOOK G7: {e}")
            import traceback
            traceback.print_exc()
    
    print("⚡" * 20 + "\n")
    return "OK", 200

# ===============================
# LOCKS PARA THREAD SAFETY
# ===============================
webhook_lock = threading.Lock()
estoque_lock = threading.Lock()
carrinhos_ativos = {}

# ===============================
# INICIAR BOT E SERVIDOR FLASK
# ===============================
def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    bot.run(DISCORD_TOKEN)
