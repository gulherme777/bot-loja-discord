# -*- coding: utf-8 -*-
"""
G7 STORE - SISTEMA PROFISSIONAL DE VENDAS AUTOMÁTICAS
Versão: 4.0.0 - COMPLETA
Todos os comandos do sistema
"""

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from flask import Flask, request, jsonify
import threading
import asyncio
import os
import sys
import time
import json
from datetime import datetime, timedelta
import logging
import traceback
import shutil
from typing import Optional, Dict, List, Any
import random
import string

# ===============================
# CONFIGURAÇÃO DE LOGS
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

logger.info("🚀 INICIANDO G7 STORE COMPLETA...")

# ===============================
# CONFIGURAÇÕES
# ===============================
class Config:
    """Configurações globais do sistema"""
    DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
    INFINITE_TAG = os.environ.get("INFINITE_TAG", "guilherme_vinicius90")
    PORT = int(os.environ.get("PORT", 10000))
    
    # IDs dos canais (podem ser alterados via comando)
    CANAL_CARRINHOS = 1513770446158303304
    CANAL_PAGOS = 1513770547933089852
    ADMIN_ID = 1431125477069688953
    ADMINS = [1431125477069688953]  # Lista de admins
    
    # URLs
    BASE_URL = "https://api.checkout.infinitepay.io"
    
    # WEBHOOK
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
    if not WEBHOOK_URL and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
        WEBHOOK_URL = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"
    
    # Arquivos
    PRODUTOS_FILE = "produtos.json"
    ESTOQUE_FILE = "estoque.json"
    PAGAMENTOS_FILE = "pagamentos.json"
    VENDAS_FILE = "vendas.json"
    CONFIG_FILE = "config.json"
    ADMINS_FILE = "admins.json"
    
    # Timeouts
    TIMEOUT_PAGAMENTO = 300
    TIMEOUT_ENTREGA = 30
    
    # Configurações da loja
    NOME_LOJA = "G7 Store"
    COR_PRINCIPAL = 0x5865F2
    COR_SUCESSO = 0x00ff88
    COR_ERRO = 0xff0000
    COR_AVISO = 0xffaa00

config = Config()

# ===============================
# CARREGAR CONFIGURAÇÕES
# ===============================
def carregar_config():
    if os.path.exists(config.CONFIG_FILE):
        try:
            with open(config.CONFIG_FILE, 'r') as f:
                data = json.load(f)
                config.CANAL_CARRINHOS = data.get('canal_carrinhos', config.CANAL_CARRINHOS)
                config.CANAL_PAGOS = data.get('canal_pagos', config.CANAL_PAGOS)
                config.ADMINS = data.get('admins', config.ADMINS)
                logger.info("✅ Configurações carregadas")
        except Exception as e:
            logger.error(f"❌ Erro ao carregar config: {e}")

def salvar_config():
    try:
        data = {
            'canal_carrinhos': config.CANAL_CARRINHOS,
            'canal_pagos': config.CANAL_PAGOS,
            'admins': config.ADMINS
        }
        with open(config.CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"❌ Erro ao salvar config: {e}")
        return False

carregar_config()

# ===============================
# GERENCIADOR DE DADOS
# ===============================
class DataManager:
    """Gerencia todos os dados da loja"""
    
    def __init__(self):
        self.produtos = {}
        self.estoque = {}
        self.pagamentos = set()
        self.vendas = []
        self.lock = threading.Lock()
        self.pedidos_ativos = {}
        self._carregar_dados()
    
    def _carregar_dados(self):
        self.produtos = self._carregar_json(config.PRODUTOS_FILE, {})
        self.estoque = self._carregar_json(config.ESTOQUE_FILE, {})
        self.pagamentos = set(self._carregar_json(config.PAGAMENTOS_FILE, []))
        self.vendas = self._carregar_json(config.VENDAS_FILE, [])
        self.pedidos_ativos = self._carregar_json("pedidos_ativos.json", {})
        logger.info(f"📊 Dados carregados: {len(self.produtos)} produtos, {len(self.vendas)} vendas")
    
    def _carregar_json(self, arquivo, default):
        if os.path.exists(arquivo):
            try:
                with open(arquivo, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"❌ Erro ao carregar {arquivo}: {e}")
                return default
        return default
    
    def _salvar_json(self, arquivo, dados):
        try:
            with open(arquivo, 'w', encoding='utf-8') as f:
                json.dump(dados, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"❌ Erro ao salvar {arquivo}: {e}")
            return False
    
    def salvar_todos(self):
        with self.lock:
            self._salvar_json(config.PRODUTOS_FILE, self.produtos)
            self._salvar_json(config.ESTOQUE_FILE, self.estoque)
            self._salvar_json(config.PAGAMENTOS_FILE, list(self.pagamentos))
            self._salvar_json(config.VENDAS_FILE, self.vendas)
            self._salvar_json("pedidos_ativos.json", self.pedidos_ativos)
    
    def get_produto(self, produto_id: str) -> Optional[Dict]:
        return self.produtos.get(produto_id)
    
    def get_estoque(self, produto_id: str, variacao: str = None) -> int:
        if produto_id not in self.estoque:
            return 0
        if variacao and variacao != "NONE":
            return len(self.estoque[produto_id].get("variacoes", {}).get(variacao, []))
        total = len(self.estoque[produto_id].get("itens", []))
        for v in self.estoque[produto_id].get("variacoes", {}).values():
            total += len(v)
        return total
    
    def baixar_estoque(self, produto_id: str, variacao: str = None) -> Optional[str]:
        with self.lock:
            if produto_id not in self.estoque:
                return None
            if variacao and variacao != "NONE":
                itens = self.estoque[produto_id].get("variacoes", {}).get(variacao, [])
                if itens:
                    item = itens.pop(0)
                    self.salvar_todos()
                    logger.info(f"✅ Item removido da variação {variacao}: {item}")
                    return item
            else:
                itens = self.estoque[produto_id].get("itens", [])
                if itens:
                    item = itens.pop(0)
                    self.salvar_todos()
                    logger.info(f"✅ Item removido do estoque simples: {item}")
                    return item
            return None
    
    def remover_itens_estoque(self, produto_id: str, indices: List[int], variacao: str = None) -> bool:
        with self.lock:
            if produto_id not in self.estoque:
                return False
            try:
                if variacao and variacao != "NONE":
                    itens = self.estoque[produto_id].get("variacoes", {}).get(variacao, [])
                    for i in sorted(indices, reverse=True):
                        if i < len(itens):
                            del itens[i]
                else:
                    itens = self.estoque[produto_id].get("itens", [])
                    for i in sorted(indices, reverse=True):
                        if i < len(itens):
                            del itens[i]
                self.salvar_todos()
                return True
            except Exception as e:
                logger.error(f"❌ Erro ao remover itens: {e}")
                return False
    
    def registrar_venda(self, venda_data: Dict):
        with self.lock:
            venda_data['id'] = len(self.vendas) + 1
            venda_data['timestamp'] = datetime.now().isoformat()
            self.vendas.append(venda_data)
            self.salvar_todos()
            logger.info(f"💰 Venda registrada: #{venda_data['id']} - {venda_data.get('produto')}")
    
    def buscar_venda(self, termo: str) -> List[Dict]:
        resultados = []
        termo = termo.lower()
        for venda in self.vendas:
            if (termo in str(venda.get('id', '')).lower() or
                termo in venda.get('produto', '').lower() or
                termo in str(venda.get('usuario_id', '')).lower() or
                termo in venda.get('slug', '').lower()):
                resultados.append(venda)
        return resultados
    
    def criar_backup(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = f"backup_{timestamp}"
        os.makedirs(backup_dir, exist_ok=True)
        
        arquivos = [config.PRODUTOS_FILE, config.ESTOQUE_FILE, config.PAGAMENTOS_FILE, 
                   config.VENDAS_FILE, config.CONFIG_FILE, "pedidos_ativos.json"]
        
        for arquivo in arquivos:
            if os.path.exists(arquivo):
                shutil.copy2(arquivo, f"{backup_dir}/{arquivo}")
        
        logger.info(f"✅ Backup criado: {backup_dir}")
        return backup_dir

data_manager = DataManager()

# ===============================
# SISTEMA DE ENTREGA (MANTIDO)
# ===============================
class DeliverySystem:
    def __init__(self, bot):
        self.bot = bot
        self.fila_entregas = asyncio.Queue()
        self.processando = False
    
    async def iniciar_processamento(self):
        if self.processando:
            return
        self.processando = True
        logger.info("🔄 Iniciando processador de entregas...")
        while True:
            try:
                entrega = await self.fila_entregas.get()
                await self._processar_entrega(entrega)
                self.fila_entregas.task_done()
            except Exception as e:
                logger.error(f"❌ Erro no processador: {e}")
                await asyncio.sleep(1)
    
    async def adicionar_entrega(self, entrega_data: Dict):
        await self.fila_entregas.put(entrega_data)
        logger.info(f"📦 Entrega adicionada à fila: {entrega_data.get('produto')}")
    
    async def _processar_entrega(self, entrega: Dict):
        try:
            produto_id = entrega.get('produto_id')
            usuario_id = entrega.get('usuario_id')
            variacao = entrega.get('variacao', 'NONE')
            valor = entrega.get('valor', 0)
            slug = entrega.get('slug')
            
            logger.info(f"🚀 Processando entrega para usuário {usuario_id}")
            
            try:
                usuario = await self.bot.fetch_user(usuario_id)
            except Exception as e:
                logger.error(f"❌ Não foi possível buscar usuário {usuario_id}: {e}")
                return
            
            produto = data_manager.get_produto(produto_id)
            if not produto:
                logger.error(f"❌ Produto {produto_id} não encontrado")
                await self._notificar_erro(usuario, "Produto não encontrado")
                return
            
            if variacao != "NONE":
                nome_produto = f"{produto['nome']} ({variacao})"
            else:
                nome_produto = produto['nome']
            
            item = data_manager.baixar_estoque(produto_id, variacao)
            
            venda = {
                "produto": nome_produto,
                "produto_id": produto_id,
                "usuario_id": usuario_id,
                "usuario": str(usuario),
                "valor": valor,
                "slug": slug,
                "entregue": bool(item),
                "item": item,
                "variacao": variacao
            }
            data_manager.registrar_venda(venda)
            
            await self._notificar_canais(usuario, nome_produto, valor, slug, item)
            
            if item:
                await self._entregar_cliente(usuario, nome_produto, item)
                logger.info(f"✅ Entrega concluída para {usuario.name}")
            else:
                await self._estoque_esgotado(usuario, nome_produto, slug)
                logger.warning(f"⚠️ Estoque esgotado para {nome_produto}")
            
        except Exception as e:
            logger.error(f"❌ Erro fatal no processamento: {e}")
            traceback.print_exc()
    
    async def _entregar_cliente(self, usuario: discord.User, produto: str, item: str):
        try:
            embed = discord.Embed(
                title="🎁 **ENTREGA REALIZADA COM SUCESSO!**",
                description="Seu pagamento foi confirmado e seu produto está pronto!",
                color=0x00ff88,
                timestamp=datetime.now()
            )
            embed.add_field(name="📦 **Produto**", value=f"```{produto}```", inline=False)
            embed.add_field(name="🔐 **Seu Código/Item**", value=f"```\n{item}\n```", inline=False)
            embed.add_field(name="✅ **Status**", value="Pagamento confirmado e item entregue!", inline=False)
            embed.set_footer(text=f"{config.NOME_LOJA} - Obrigado pela preferência! ❤️")
            await usuario.send(embed=embed)
            logger.info(f"📨 Item entregue para {usuario.name}")
        except discord.Forbidden:
            logger.warning(f"⚠️ Não foi possível enviar DM para {usuario.name}")
            await self._enviar_no_suporte(usuario, produto, item)
        except Exception as e:
            logger.error(f"❌ Erro ao entregar: {e}")
    
    async def _notificar_canais(self, usuario, produto, valor, slug, item):
        canal_pagos = self.bot.get_channel(config.CANAL_PAGOS)
        if canal_pagos:
            embed = discord.Embed(
                title="✅ **NOVO PAGAMENTO CONFIRMADO**",
                color=0x00ff88,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 **Cliente**", value=f"{usuario.mention}\n`{usuario.id}`", inline=False)
            embed.add_field(name="📦 **Produto**", value=f"```{produto}```", inline=True)
            embed.add_field(name="💰 **Valor**", value=f"```R$ {valor:.2f}```", inline=True)
            embed.add_field(name="🆔 **Transação**", value=f"```{slug}```", inline=False)
            if item:
                embed.add_field(name="🔐 **Item Entregue**", value=f"```\n{item}\n```", inline=False)
            else:
                embed.add_field(
                    name="⚠️ **ATENÇÃO**", 
                    value="```O estoque está esgotado! Entrega manual necessária!```", 
                    inline=False
                )
            embed.set_footer(text=f"{config.NOME_LOJA} - Sistema Automático")
            try:
                await canal_pagos.send(embed=embed)
            except Exception as e:
                logger.error(f"❌ Erro ao enviar para canal de pagamentos: {e}")
    
    async def _estoque_esgotado(self, usuario, produto, slug):
        try:
            admin = await self.bot.fetch_user(config.ADMIN_ID)
            embed = discord.Embed(
                title="🚨 **ESTOQUE ESGOTADO!**",
                description="Um cliente pagou mas não havia estoque disponível!",
                color=0xff0000,
                timestamp=datetime.now()
            )
            embed.add_field(name="👤 Cliente", value=usuario.mention, inline=False)
            embed.add_field(name="📦 Produto", value=f"```{produto}```", inline=False)
            embed.add_field(name="🆔 Slug", value=f"```{slug}```", inline=True)
            await admin.send(embed=embed)
            
            embed = discord.Embed(
                title="✅ **PAGAMENTO CONFIRMADO**",
                description="Seu pagamento foi confirmado com sucesso!",
                color=0xffaa00,
                timestamp=datetime.now()
            )
            embed.add_field(name="📦 Produto", value=f"```{produto}```", inline=False)
            embed.add_field(
                name="⚠️ **ATENÇÃO**", 
                value="O estoque acabou no momento da compra, mas o administrador já foi notificado e fará sua entrega manual em até 5 minutos!",
                inline=False
            )
            await usuario.send(embed=embed)
        except Exception as e:
            logger.error(f"❌ Erro no estoque esgotado: {e}")
    
    async def _notificar_erro(self, usuario, erro):
        try:
            embed = discord.Embed(
                title="❌ **ERRO NA ENTREGA**",
                color=0xff0000,
                timestamp=datetime.now()
            )
            embed.add_field(name="⚠️ Erro", value=f"```{erro}```", inline=False)
            await usuario.send(embed=embed)
        except:
            pass
    
    async def _enviar_no_suporte(self, usuario, produto, item):
        try:
            canal_suporte = self.bot.get_channel(config.CANAL_PAGOS)
            if canal_suporte:
                embed = discord.Embed(
                    title="📨 ENTREGA POR CANAL (DM BLOQUEADA)",
                    color=0xffaa00,
                    timestamp=datetime.now()
                )
                embed.add_field(name="👤 Cliente", value=f"{usuario.mention} `{usuario.id}`", inline=False)
                embed.add_field(name="📦 Produto", value=f"```{produto}```", inline=False)
                embed.add_field(name="🔐 Item", value=f"```\n{item}\n```", inline=False)
                await canal_suporte.send(embed=embed)
        except Exception as e:
            logger.error(f"❌ Erro ao enviar no suporte: {e}")

# ===============================
# BOT PRINCIPAL
# ===============================
class G7StoreBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.delivery = DeliverySystem(self)
        self.loop_entregas = None
    
    async def setup_hook(self):
        try:
            await self.tree.sync()
            logger.info("✅ Comandos sincronizados!")
            self.loop_entregas = asyncio.create_task(self.delivery.iniciar_processamento())
        except Exception as e:
            logger.error(f"❌ Erro no setup: {e}")
    
    async def on_ready(self):
        logger.info(f"🟢 Bot Online: {self.user}")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(data_manager.produtos)} produtos | {config.NOME_LOJA}"
            )
        )

bot = G7StoreBot()
carrinhos_ativos = {}

# ===============================
# WEBHOOK FLASK
# ===============================
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook_receiver():
    try:
        data = request.json
        logger.info(f"📨 Webhook recebido")
        
        if not data or data.get("status") != "paid":
            return "OK", 200
        
        slug = data.get("invoice_slug")
        with data_manager.lock:
            if slug in data_manager.pagamentos:
                return "OK", 200
            data_manager.pagamentos.add(slug)
            data_manager.salvar_todos()
        
        nsu = data.get("order_nsu", "")
        parts = nsu.split('|')
        if len(parts) < 3:
            return "OK", 200
        
        entrega_data = {
            "produto_id": parts[0],
            "usuario_id": int(parts[1]),
            "variacao": parts[2] if len(parts) > 2 else "NONE",
            "valor": float(data.get("amount", 0)) / 100,
            "slug": slug
        }
        
        asyncio.run_coroutine_threadsafe(
            bot.delivery.adicionar_entrega(entrega_data),
            bot.loop
        )
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"❌ Erro no webhook: {e}")
        return jsonify({"status": "error"}), 500

@flask_app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "bot": str(bot.user),
        "produtos": len(data_manager.produtos),
        "vendas": len(data_manager.vendas)
    })

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

# ===============================
# FUNÇÃO GERAR LINK
# ===============================
async def gerar_link_pagamento(inter, p_id, preco, nome_completo, var_nome="NONE"):
    await inter.response.defer(ephemeral=True)
    
    nsu = f"{p_id}|{inter.user.id}|{var_nome}|{int(time.time())}"
    payload = {
        "handle": config.INFINITE_TAG,
        "order_nsu": nsu,
        "items": [{"quantity": 1, "price": int(preco * 100), "description": nome_completo[:60]}]
    }
    if config.WEBHOOK_URL:
        payload["webhook_url"] = config.WEBHOOK_URL
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{config.BASE_URL}/links", json=payload, timeout=30) as resp:
                if resp.status in [200, 201]:
                    data = await resp.json()
                    pay_url = data.get("url")
                    pay_id = data.get("invoice_slug")
                    
                    canal_carrinhos = bot.get_channel(config.CANAL_CARRINHOS)
                    if canal_carrinhos:
                        embed = discord.Embed(
                            title="🛒 **NOVO CARRINHO**",
                            color=0xffaa00,
                            timestamp=datetime.now()
                        )
                        embed.add_field(name="👤 Cliente", value=inter.user.mention, inline=False)
                        embed.add_field(name="📦 Produto", value=f"```{nome_completo}```", inline=False)
                        embed.add_field(name="💰 Valor", value=f"```R$ {preco:.2f}```", inline=True)
                        msg = await canal_carrinhos.send(embed=embed)
                        carrinhos_ativos[str(pay_id)] = msg.id
                    
                    embed = discord.Embed(
                        title="💳 **LINK DE PAGAMENTO GERADO!**",
                        color=0x00ff88,
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="💰 Valor", value=f"```R$ {preco:.2f}```", inline=False)
                    embed.set_footer(text=f"{config.NOME_LOJA} - Pagamento Seguro")
                    
                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(
                        label="💳 PAGAR AGORA",
                        style=discord.ButtonStyle.link,
                        url=pay_url
                    ))
                    
                    await inter.user.send(embed=embed, view=view)
                    await inter.followup.send("📨 Link enviado no seu privado!", ephemeral=True)
                else:
                    await inter.followup.send("❌ Erro ao gerar link.", ephemeral=True)
    except Exception as e:
        logger.error(f"❌ Erro ao gerar link: {e}")
        await inter.followup.send("❌ Erro interno!", ephemeral=True)

# ===============================
# VERIFICAÇÃO DE ADMIN
# ===============================
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMINS or user_id == config.ADMIN_ID

# ===============================
# COMANDOS ADMINISTRATIVOS
# ===============================

@bot.tree.command(name="criar_produto", description="[ADMIN] Criar um novo produto")
@app_commands.describe(
    id="ID único do produto",
    nome="Nome do produto",
    preco="Preço em reais",
    descricao="Descrição do produto",
    tipo="Tipo: auto ou manual"
)
async def cmd_criar_produto(interaction: discord.Interaction, id: str, nome: str, preco: float, descricao: str, tipo: str = "auto"):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if id in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto já existe!**", ephemeral=True)
    
    data_manager.produtos[id] = {
        "nome": nome,
        "preco": preco,
        "descricao": descricao,
        "tipo": tipo,
        "variacoes": [],
        "criado_em": datetime.now().isoformat(),
        "criado_por": str(interaction.user)
    }
    
    if id not in data_manager.estoque:
        data_manager.estoque[id] = {"itens": [], "variacoes": {}}
    
    data_manager.salvar_todos()
    
    embed = discord.Embed(title="✅ **PRODUTO CRIADO!**", color=0x00ff88, timestamp=datetime.now())
    embed.add_field(name="🆔 ID", value=f"```{id}```", inline=False)
    embed.add_field(name="📦 Nome", value=f"```{nome}```", inline=True)
    embed.add_field(name="💰 Preço", value=f"```R$ {preco:.2f}```", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remover_produto", description="[ADMIN] Remover um produto")
@app_commands.describe(produto_id="ID do produto")
async def cmd_remover_produto(interaction: discord.Interaction, produto_id: str):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    produto = data_manager.produtos[produto_id]
    
    embed = discord.Embed(
        title="⚠️ **CONFIRMAR REMOÇÃO**",
        description=f"Tem certeza que deseja remover o produto **{produto['nome']}**?",
        color=0xff0000
    )
    embed.add_field(name="🆔 ID", value=f"```{produto_id}```", inline=False)
    embed.add_field(name="💰 Preço", value=f"```R$ {produto['preco']:.2f}```", inline=True)
    embed.add_field(name="📦 Estoque", value=f"```{data_manager.get_estoque(produto_id)} unidades```", inline=True)
    
    view = discord.ui.View()
    
    async def confirmar(inter: discord.Interaction):
        if not is_admin(inter.user.id):
            return await inter.response.send_message("❌ Sem permissão!", ephemeral=True)
        
        del data_manager.produtos[produto_id]
        if produto_id in data_manager.estoque:
            del data_manager.estoque[produto_id]
        data_manager.salvar_todos()
        
        await inter.response.edit_message(
            content=f"✅ **Produto `{produto_id}` removido com sucesso!**",
            embed=None,
            view=None
        )
    
    async def cancelar(inter: discord.Interaction):
        if not is_admin(inter.user.id):
            return await inter.response.send_message("❌ Sem permissão!", ephemeral=True)
        await inter.response.edit_message(
            content="❌ **Operação cancelada.**",
            embed=None,
            view=None
        )
    
    view.add_item(discord.ui.Button(label="✅ Confirmar", style=discord.ButtonStyle.danger, custom_id="confirmar"))
    view.add_item(discord.ui.Button(label="❌ Cancelar", style=discord.ButtonStyle.secondary, custom_id="cancelar"))
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="editar_produto", description="[ADMIN] Editar informações de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    nome="Novo nome (opcional)",
    preco="Novo preço (opcional)",
    descricao="Nova descrição (opcional)",
    tipo="Novo tipo (opcional)"
)
async def cmd_editar_produto(interaction: discord.Interaction, produto_id: str, nome: str = None, preco: float = None, descricao: str = None, tipo: str = None):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    produto = data_manager.produtos[produto_id]
    
    if nome:
        produto['nome'] = nome
    if preco is not None:
        produto['preco'] = preco
    if descricao:
        produto['descricao'] = descricao
    if tipo:
        produto['tipo'] = tipo
    
    data_manager.salvar_todos()
    
    embed = discord.Embed(title="✅ **PRODUTO ATUALIZADO!**", color=0x00ff88, timestamp=datetime.now())
    embed.add_field(name="🆔 ID", value=f"```{produto_id}```", inline=False)
    embed.add_field(name="📦 Nome", value=f"```{produto['nome']}```", inline=True)
    embed.add_field(name="💰 Preço", value=f"```R$ {produto['preco']:.2f}```", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="add_estoque", description="[ADMIN] Adicionar itens ao estoque")
@app_commands.describe(
    produto_id="ID do produto",
    itens="Itens separados por |",
    variacao="Nome da variação (opcional)"
)
async def cmd_add_estoque(interaction: discord.Interaction, produto_id: str, itens: str, variacao: Optional[str] = None):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    novos = [i.strip() for i in itens.split("|") if i.strip()]
    if not novos:
        return await interaction.response.send_message("❌ **Nenhum item válido!**", ephemeral=True)
    
    if produto_id not in data_manager.estoque:
        data_manager.estoque[produto_id] = {"itens": [], "variacoes": {}}
    
    if variacao and variacao != "NONE":
        if "variacoes" not in data_manager.estoque[produto_id]:
            data_manager.estoque[produto_id]["variacoes"] = {}
        if variacao not in data_manager.estoque[produto_id]["variacoes"]:
            data_manager.estoque[produto_id]["variacoes"][variacao] = []
        data_manager.estoque[produto_id]["variacoes"][variacao].extend(novos)
    else:
        data_manager.estoque[produto_id]["itens"].extend(novos)
    
    data_manager.salvar_todos()
    
    total = data_manager.get_estoque(produto_id)
    embed = discord.Embed(title="✅ **ESTOQUE ATUALIZADO!**", color=0x00ff88, timestamp=datetime.now())
    embed.add_field(name="📦 Produto", value=f"```{produto_id}```", inline=False)
    embed.add_field(name="➕ Adicionados", value=f"```{len(novos)}```", inline=True)
    embed.add_field(name="📊 Total", value=f"```{total}```", inline=True)
    if variacao:
        embed.add_field(name="🎯 Variação", value=f"```{variacao}```", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remover_estoque", description="[ADMIN] Remover itens específicos do estoque")
@app_commands.describe(
    produto_id="ID do produto",
    indices="Índices dos itens para remover (ex: 0,1,2)",
    variacao="Nome da variação (opcional)"
)
async def cmd_remover_estoque(interaction: discord.Interaction, produto_id: str, indices: str, variacao: Optional[str] = None):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    try:
        indices_list = [int(i.strip()) for i in indices.split(',') if i.strip()]
    except ValueError:
        return await interaction.response.send_message("❌ **Índices inválidos!** Use: 0,1,2", ephemeral=True)
    
    if data_manager.remover_itens_estoque(produto_id, indices_list, variacao):
        total = data_manager.get_estoque(produto_id)
        await interaction.response.send_message(f"✅ **{len(indices_list)} itens removidos!** Total agora: {total}", ephemeral=True)
    else:
        await interaction.response.send_message("❌ **Erro ao remover itens!**", ephemeral=True)

@bot.tree.command(name="limpar_estoque", description="[ADMIN] Limpar todo o estoque de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    confirmar="Digite 'SIM' para confirmar"
)
async def cmd_limpar_estoque(interaction: discord.Interaction, produto_id: str, confirmar: str = "NÃO"):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    if confirmar != "SIM":
        return await interaction.response.send_message(
            f"⚠️ **Para confirmar, digite:** `/limpar_estoque produto_id:{produto_id} confirmar:SIM`",
            ephemeral=True
        )
    
    if produto_id in data_manager.estoque:
        data_manager.estoque[produto_id] = {"itens": [], "variacoes": {}}
        data_manager.salvar_todos()
        await interaction.response.send_message(f"✅ **Estoque de `{produto_id}` foi completamente limpo!**", ephemeral=True)

@bot.tree.command(name="add_variacao", description="[ADMIN] Adicionar variação a um produto")
@app_commands.describe(
    produto_id="ID do produto",
    nome="Nome da variação",
    preco="Preço da variação"
)
async def cmd_add_variacao(interaction: discord.Interaction, produto_id: str, nome: str, preco: float):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    if "variacoes" not in data_manager.produtos[produto_id]:
        data_manager.produtos[produto_id]["variacoes"] = []
    
    data_manager.produtos[produto_id]["variacoes"].append({"nome": nome, "preco": preco})
    
    if produto_id not in data_manager.estoque:
        data_manager.estoque[produto_id] = {"itens": [], "variacoes": {}}
    if "variacoes" not in data_manager.estoque[produto_id]:
        data_manager.estoque[produto_id]["variacoes"] = {}
    if nome not in data_manager.estoque[produto_id]["variacoes"]:
        data_manager.estoque[produto_id]["variacoes"][nome] = []
    
    data_manager.salvar_todos()
    
    embed = discord.Embed(title="✅ **VARIAÇÃO ADICIONADA!**", color=0x00ff88)
    embed.add_field(name="📦 Produto", value=f"```{produto_id}```", inline=False)
    embed.add_field(name="🎯 Variação", value=f"```{nome}```", inline=True)
    embed.add_field(name="💰 Preço", value=f"```R$ {preco:.2f}```", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="sincronizar", description="[ADMIN] Enviar painel de vendas")
@app_commands.describe(
    canal="Canal onde será enviado",
    produto_id="ID do produto"
)
async def cmd_sincronizar(interaction: discord.Interaction, canal: discord.TextChannel, produto_id: str):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    produto = data_manager.get_produto(produto_id)
    if not produto:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    estoque_total = data_manager.get_estoque(produto_id)
    
    embed = discord.Embed(
        title=f"🛒 **{produto['nome']}**",
        description=f"{produto['descricao']}\n\n📦 **Estoque:** `{estoque_total}` unidades",
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.add_field(name="💰 **Preço**", value=f"```R$ {produto['preco']:.2f}```", inline=False)
    
    if produto.get("imagem"):
        embed.set_image(url=produto["imagem"])
        
    embed.set_footer(text=f"{config.NOME_LOJA} - Clique em Comprar")
    
    view = discord.ui.View(timeout=None)
    btn = discord.ui.Button(label="🛒 COMPRAR", style=discord.ButtonStyle.success, custom_id=f"buy_{produto_id}")
    
    async def buy_callback(inter: discord.Interaction):
        produto_atual = data_manager.get_produto(produto_id)
        if not produto_atual:
            return await inter.response.send_message("❌ Produto não encontrado!", ephemeral=True)
        
        variacoes = produto_atual.get("variacoes", [])
        if variacoes:
            select = discord.ui.Select(placeholder="📋 Selecione uma variação...")
            for v in variacoes:
                select.add_option(label=v["nome"], value=v["nome"], description=f"R$ {v['preco']:.2f}")
            
            async def select_callback(s_inter: discord.Interaction):
                var_selecionada = select.values[0]
                for v in variacoes:
                    if v["nome"] == var_selecionada:
                        await gerar_link_pagamento(s_inter, produto_id, v["preco"], f"{produto_atual['nome']} ({var_selecionada})", var_selecionada)
                        break
            
            select.callback = select_callback
            view_var = discord.ui.View()
            view_var.add_item(select)
            await inter.response.send_message("📋 **Selecione a opção desejada:**", view=view_var, ephemeral=True)
        else:
            await gerar_link_pagamento(inter, produto_id, produto_atual["preco"], produto_atual["nome"], "NONE")
    
    btn.callback = buy_callback
    view.add_item(btn)
    
    await canal.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ **Painel enviado para {canal.mention}!**", ephemeral=True)

@bot.tree.command(name="estoque", description="[ADMIN] Verificar estoque")
@app_commands.describe(produto_id="ID do produto")
async def cmd_estoque(interaction: discord.Interaction, produto_id: str):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    produto = data_manager.produtos[produto_id]
    total = data_manager.get_estoque(produto_id)
    estoque_data = data_manager.estoque.get(produto_id, {"itens": [], "variacoes": {}})
    
    embed = discord.Embed(title=f"📊 **ESTOQUE - {produto['nome']}**", color=0x5865F2, timestamp=datetime.now())
    embed.add_field(name="📦 Total", value=f"```{total} unidades```", inline=False)
    embed.add_field(name="📋 Simples", value=f"```{len(estoque_data.get('itens', []))} unidades```", inline=True)
    
    variacoes_texto = ""
    for nome, itens in estoque_data.get("variacoes", {}).items():
        variacoes_texto += f"• {nome}: {len(itens)} unidades\n"
    
    if variacoes_texto:
        embed.add_field(name="🎯 Variações", value=f"```{variacoes_texto}```", inline=False)
    
    # Mostrar primeiros 5 itens
    itens_simples = estoque_data.get("itens", [])[:5]
    if itens_simples:
        embed.add_field(name="🔐 Itens (primeiros 5)", value=f"```\n{chr(10).join(itens_simples)}\n```", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="vendas", description="[ADMIN] Ver histórico de vendas")
@app_commands.describe(limite="Número de vendas para mostrar")
async def cmd_vendas(interaction: discord.Interaction, limite: int = 10):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    vendas = data_manager.vendas[-limite:] if limite > 0 else data_manager.vendas
    
    if not vendas:
        return await interaction.response.send_message("📭 **Nenhuma venda registrada ainda!**", ephemeral=True)
    
    embed = discord.Embed(
        title=f"📊 **HISTÓRICO DE VENDAS**",
        description=f"Últimas {len(vendas)} vendas",
        color=0xffaa00,
        timestamp=datetime.now()
    )
    
    for venda in reversed(vendas):
        data = datetime.fromisoformat(venda.get("data", "2000-01-01")).strftime("%d/%m %H:%M")
        status = "✅" if venda.get("entregue") else "⚠️"
        embed.add_field(
            name=f"{status} #{venda.get('id', '?')} - {venda['produto']}",
            value=f"R$ {venda['valor']:.2f} | {data} | {venda.get('usuario', 'Unknown')[:20]}",
            inline=False
        )
    
    total_vendas = sum(v.get("valor", 0) for v in vendas)
    embed.set_footer(text=f"💰 Total: R$ {total_vendas:.2f} | Total: {len(data_manager.vendas)}")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="buscar_pedido", description="[ADMIN] Buscar pedido por ID ou slug")
@app_commands.describe(termo="ID do pedido, slug ou nome do produto")
async def cmd_buscar_pedido(interaction: discord.Interaction, termo: str):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    resultados = data_manager.buscar_venda(termo)
    
    if not resultados:
        return await interaction.response.send_message(f"🔍 **Nenhum pedido encontrado para:** `{termo}`", ephemeral=True)
    
    embed = discord.Embed(
        title=f"🔍 **PEDIDOS ENCONTRADOS**",
        description=f"{len(resultados)} resultado(s) para `{termo}`",
        color=0x5865F2,
        timestamp=datetime.now()
    )
    
    for venda in resultados[:10]:
        data = datetime.fromisoformat(venda.get("data", "2000-01-01")).strftime("%d/%m %H:%M")
        embed.add_field(
            name=f"#{venda.get('id')} - {venda['produto']}",
            value=f"👤 {venda.get('usuario', 'Unknown')}\n💰 R$ {venda['valor']:.2f}\n📅 {data}\n🆔 {venda.get('slug', 'N/A')}",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="cancelar_pedido", description="[ADMIN] Cancelar pedido")
@app_commands.describe(
    pedido_id="ID do pedido",
    motivo="Motivo do cancelamento"
)
async def cmd_cancelar_pedido(interaction: discord.Interaction, pedido_id: int, motivo: str = "Cancelado pelo administrador"):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    # Encontrar o pedido
    pedido = None
    for venda in data_manager.vendas:
        if venda.get('id') == pedido_id:
            pedido = venda
            break
    
    if not pedido:
        return await interaction.response.send_message(f"❌ **Pedido #{pedido_id} não encontrado!**", ephemeral=True)
    
    if pedido.get('cancelado'):
        return await interaction.response.send_message(f"⚠️ **Pedido #{pedido_id} já foi cancelado!**", ephemeral=True)
    
    # Confirmar
    embed = discord.Embed(
        title=f"⚠️ **CANCELAR PEDIDO #{pedido_id}**",
        description=f"Tem certeza que deseja cancelar este pedido?",
        color=0xff0000,
        timestamp=datetime.now()
    )
    embed.add_field(name="📦 Produto", value=f"```{pedido['produto']}```", inline=False)
    embed.add_field(name="👤 Cliente", value=f"```{pedido.get('usuario', 'Unknown')}```", inline=True)
    embed.add_field(name="💰 Valor", value=f"```R$ {pedido['valor']:.2f}```", inline=True)
    embed.add_field(name="📝 Motivo", value=f"```{motivo}```", inline=False)
    
    view = discord.ui.View()
    
    async def confirmar(inter: discord.Interaction):
        if not is_admin(inter.user.id):
            return await inter.response.send_message("❌ Sem permissão!", ephemeral=True)
        
        pedido['cancelado'] = True
        pedido['motivo_cancelamento'] = motivo
        pedido['cancelado_por'] = str(inter.user)
        data_manager.salvar_todos()
        
        # Notificar cliente
        try:
            usuario = await bot.fetch_user(pedido.get('usuario_id'))
            embed_cliente = discord.Embed(
                title="❌ **PEDIDO CANCELADO**",
                description=f"Seu pedido foi cancelado.",
                color=0xff0000,
                timestamp=datetime.now()
            )
            embed_cliente.add_field(name="📦 Produto", value=f"```{pedido['produto']}```", inline=False)
            embed_cliente.add_field(name="📝 Motivo", value=f"```{motivo}```", inline=False)
            embed_cliente.add_field(name="💳 Reembolso", value="```O reembolso será processado em até 5 dias úteis.```", inline=False)
            await usuario.send(embed=embed_cliente)
        except:
            pass
        
        await inter.response.edit_message(
            content=f"✅ **Pedido #{pedido_id} cancelado com sucesso!**",
            embed=None,
            view=None
        )
    
    async def cancelar(inter: discord.Interaction):
        if not is_admin(inter.user.id):
            return await inter.response.send_message("❌ Sem permissão!", ephemeral=True)
        await inter.response.edit_message(
            content="❌ **Operação cancelada.**",
            embed=None,
            view=None
        )
    
    view.add_item(discord.ui.Button(label="✅ Confirmar", style=discord.ButtonStyle.danger, custom_id="confirmar"))
    view.add_item(discord.ui.Button(label="❌ Cancelar", style=discord.ButtonStyle.secondary, custom_id="cancelar"))
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="setar_canal", description="[ADMIN] Configurar canais da loja")
@app_commands.describe(
    tipo="Tipo de canal: carrinhos ou pagos",
    canal="Canal para configurar"
)
async def cmd_setar_canal(interaction: discord.Interaction, tipo: str, canal: discord.TextChannel):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if tipo.lower() == "carrinhos":
        config.CANAL_CARRINHOS = canal.id
        salvar_config()
        await interaction.response.send_message(f"✅ **Canal de carrinhos definido para {canal.mention}!**", ephemeral=True)
    elif tipo.lower() == "pagos" or tipo.lower() == "pagamentos":
        config.CANAL_PAGOS = canal.id
        salvar_config()
        await interaction.response.send_message(f"✅ **Canal de pagamentos definido para {canal.mention}!**", ephemeral=True)
    else:
        await interaction.response.send_message("❌ **Tipo inválido! Use:** `carrinhos` ou `pagos`", ephemeral=True)

@bot.tree.command(name="status", description="[ADMIN] Ver status completo da loja")
async def cmd_status(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    total_estoque = sum(data_manager.get_estoque(pid) for pid in data_manager.produtos)
    total_vendas = len(data_manager.vendas)
    valor_total = sum(v.get("valor", 0) for v in data_manager.vendas)
    
    embed = discord.Embed(
        title=f"📊 **STATUS DA LOJA**",
        description=f"{config.NOME_LOJA} - {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.add_field(name="📦 Produtos", value=f"```{len(data_manager.produtos)}```", inline=True)
    embed.add_field(name="📊 Estoque Total", value=f"```{total_estoque} itens```", inline=True)
    embed.add_field(name="💰 Vendas", value=f"```{total_vendas}```", inline=True)
    embed.add_field(name="💵 Faturamento", value=f"```R$ {valor_total:.2f}```", inline=True)
    embed.add_field(name="🆔 Admin", value=f"```{config.ADMIN_ID}```", inline=True)
    embed.add_field(name="📡 Webhook", value=f"```{config.WEBHOOK_URL or 'NÃO CONFIGURADO'}```", inline=False)
    embed.add_field(
        name="📋 Canais",
        value=f"🛒 Carrinhos: <#{config.CANAL_CARRINHOS}>\n✅ Pagos: <#{config.CANAL_PAGOS}>",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="backup", description="[ADMIN] Criar backup dos dados")
async def cmd_backup(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    await interaction.response.send_message("🔄 **Criando backup... Aguarde!**", ephemeral=True)
    
    backup_dir = data_manager.criar_backup()
    
    embed = discord.Embed(
        title="✅ **BACKUP CRIADO!**",
        description=f"Backup salvo em: `{backup_dir}`",
        color=0x00ff88,
        timestamp=datetime.now()
    )
    embed.add_field(name="📁 Pasta", value=f"```{backup_dir}```", inline=False)
    embed.add_field(name="📦 Produtos", value=f"```{len(data_manager.produtos)}```", inline=True)
    embed.add_field(name="💰 Vendas", value=f"```{len(data_manager.vendas)}```", inline=True)
    
    await interaction.edit_original_response(content=None, embed=embed)

@bot.tree.command(name="enviar_mensagem", description="[ADMIN] Enviar mensagem para todos os clientes")
@app_commands.describe(
    mensagem="Mensagem para enviar",
    canal="Canal para enviar (opcional)"
)
async def cmd_enviar_mensagem(interaction: discord.Interaction, mensagem: str, canal: Optional[discord.TextChannel] = None):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    await interaction.response.send_message("📨 **Enviando mensagens...**", ephemeral=True)
    
    # Coletar usuários únicos das vendas
    usuarios = set()
    for venda in data_manager.vendas:
        if venda.get('usuario_id'):
            usuarios.add(venda.get('usuario_id'))
    
    enviados = 0
    falhas = 0
    
    embed = discord.Embed(
        title="📨 **MENSAGEM DA LOJA**",
        description=mensagem,
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"{config.NOME_LOJA} - Administração")
    
    for usuario_id in usuarios:
        try:
            usuario = await bot.fetch_user(usuario_id)
            await usuario.send(embed=embed)
            enviados += 1
            await asyncio.sleep(0.5)  # Evitar rate limit
        except:
            falhas += 1
    
    # Se canal especificado, enviar lá também
    if canal:
        await canal.send(embed=embed)
    
    await interaction.edit_original_response(
        content=f"✅ **Mensagem enviada!**\n📨 Enviados: {enviados}\n❌ Falhas: {falhas}"
    )

@bot.tree.command(name="add_admin", description="[ADMIN] Adicionar administrador")
@app_commands.describe(usuario="Usuário para adicionar como admin")
async def cmd_add_admin(interaction: discord.Interaction, usuario: discord.User):
    if interaction.user.id != config.ADMIN_ID:
        return await interaction.response.send_message("❌ **Apenas o dono pode fazer isso!**", ephemeral=True)
    
    if usuario.id in config.ADMINS:
        return await interaction.response.send_message(f"⚠️ **{usuario.mention} já é administrador!**", ephemeral=True)
    
    config.ADMINS.append(usuario.id)
    salvar_config()
    
    embed = discord.Embed(
        title="✅ **ADMIN ADICIONADO!**",
        description=f"{usuario.mention} agora é administrador da loja!",
        color=0x00ff88,
        timestamp=datetime.now()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remover_admin", description="[ADMIN] Remover administrador")
@app_commands.describe(usuario="Usuário para remover")
async def cmd_remover_admin(interaction: discord.Interaction, usuario: discord.User):
    if interaction.user.id != config.ADMIN_ID:
        return await interaction.response.send_message("❌ **Apenas o dono pode fazer isso!**", ephemeral=True)
    
    if usuario.id == config.ADMIN_ID:
        return await interaction.response.send_message("❌ **Não é possível remover o dono!**", ephemeral=True)
    
    if usuario.id not in config.ADMINS:
        return await interaction.response.send_message(f"⚠️ **{usuario.mention} não é administrador!**", ephemeral=True)
    
    config.ADMINS.remove(usuario.id)
    salvar_config()
    
    embed = discord.Embed(
        title="✅ **ADMIN REMOVIDO!**",
        description=f"{usuario.mention} não é mais administrador.",
        color=0xffaa00,
        timestamp=datetime.now()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="configurar", description="[ADMIN] Configurar opções da loja")
@app_commands.describe(
    opcao="Opção para configurar",
    valor="Valor da configuração"
)
async def cmd_configurar(interaction: discord.Interaction, opcao: str, valor: str):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    opcao = opcao.lower()
    
    if opcao == "webhook":
        config.WEBHOOK_URL = valor
        await interaction.response.send_message(f"✅ **Webhook configurado:** `{valor}`", ephemeral=True)
    elif opcao == "tag":
        config.INFINITE_TAG = valor
        await interaction.response.send_message(f"✅ **Tag configurada:** `{valor}`", ephemeral=True)
    elif opcao == "nome_loja":
        config.NOME_LOJA = valor
        await interaction.response.send_message(f"✅ **Nome da loja configurado:** `{valor}`", ephemeral=True)
    else:
        await interaction.response.send_message(
            "❌ **Opção inválida! Opções disponíveis:** `webhook`, `tag`, `nome_loja`",
            ephemeral=True
        )

@bot.tree.command(name="testar_webhook", description="[ADMIN] Testar o webhook manualmente")
@app_commands.describe(
    produto_id="ID do produto",
    usuario="Usuário para testar",
    slug="ID do teste (opcional)"
)
async def cmd_testar_webhook(interaction: discord.Interaction, produto_id: str, usuario: discord.User, slug: str = "teste_123"):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    dados_teste = {
        "status": "paid",
        "invoice_slug": slug,
        "order_nsu": f"{produto_id}|{usuario.id}|NONE|{int(time.time())}",
        "amount": int(data_manager.produtos[produto_id]["preco"] * 100)
    }
    
    with data_manager.lock:
        if slug in data_manager.pagamentos:
            return await interaction.response.send_message(f"⚠️ **Slug {slug} já processado!**", ephemeral=True)
        data_manager.pagamentos.add(slug)
        data_manager.salvar_todos()
    
    await interaction.response.send_message(f"🔄 **Teste enviado para {usuario.mention}!**", ephemeral=True)
    
    entrega_data = {
        "produto_id": produto_id,
        "usuario_id": usuario.id,
        "variacao": "NONE",
        "valor": data_manager.produtos[produto_id]["preco"],
        "slug": slug
    }
    
    asyncio.run_coroutine_threadsafe(
        bot.delivery.adicionar_entrega(entrega_data),
        bot.loop
    )

@bot.tree.command(name="ping", description="Verificar latência do bot")
async def cmd_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000, 2)
    embed = discord.Embed(
        title="🏓 **PONG!**",
        description=f"Latência: `{latency}ms`",
        color=0x5865F2
    )
    embed.add_field(
        name="📊 Status",
        value=f"Produtos: {len(data_manager.produtos)}\nVendas: {len(data_manager.vendas)}",
        inline=False
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="set_imagem", description="[ADMIN] Definir a imagem de um produto")
@app_commands.describe(
    produto_id="ID do produto",
    url="Link da imagem (URL)"
)
async def cmd_set_imagem(interaction: discord.Interaction, produto_id: str, url: str):
    if not is_admin(interaction.user.id):
        return await interaction.response.send_message("❌ **Sem permissão!**", ephemeral=True)
    
    if produto_id not in data_manager.produtos:
        return await interaction.response.send_message("❌ **Produto não encontrado!**", ephemeral=True)
    
    if not (url.startswith("http://") or url.startswith("https://")):
        return await interaction.response.send_message("❌ **URL inválida!** Certifique-se que o link começa com http:// ou https://", ephemeral=True)

    data_manager.produtos[produto_id]["imagem"] = url
    data_manager.salvar_todos()
    
    embed = discord.Embed(title="✅ **IMAGEM DEFINIDA!**", color=0x00ff88, timestamp=datetime.now())
    embed.add_field(name="📦 Produto", value=f"```{data_manager.produtos[produto_id]['nome']}```", inline=True)
    embed.add_field(name="🆔 ID", value=f"```{produto_id}```", inline=True)
    embed.set_image(url=url)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ajuda", description="Mostrar todos os comandos disponíveis")
async def cmd_ajuda(interaction: discord.Interaction):
    embed = discord.Embed(
        title=f"📚 **COMANDOS {config.NOME_LOJA}**",
        description="Lista de todos os comandos disponíveis",
        color=0x5865F2,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="🛠️ **Administração**",
        value="`/criar_produto` - Criar novo produto\n"
              "`/remover_produto` - Remover produto\n"
              "`/editar_produto` - Editar produto\n"
              "`/add_estoque` - Adicionar itens\n"
              "`/remover_estoque` - Remover itens\n"
              "`/limpar_estoque` - Limpar estoque\n"
              "`/add_variacao` - Adicionar variação\n"
              "`/sincronizar` - Enviar painel\n"
              "`/setar_canal` - Configurar canais\n"
              "`/set_imagem` - Definir imagem do produto",
        inline=False
    )
    
    embed.add_field(
        name="📊 **Informações**",
        value="`/estoque` - Ver estoque\n"
              "`/vendas` - Ver vendas\n"
              "`/buscar_pedido` - Buscar pedido\n"
              "`/cancelar_pedido` - Cancelar pedido\n"
              "`/status` - Status da loja\n"
              "`/ping` - Verificar latência",
        inline=False
    )
    
    embed.add_field(
        name="🔧 **Sistema**",
        value="`/backup` - Criar backup\n"
              "`/enviar_mensagem` - Enviar mensagem\n"
              "`/add_admin` - Adicionar admin\n"
              "`/remover_admin` - Remover admin\n"
              "`/configurar` - Configurar opções\n"
              "`/testar_webhook` - Testar webhook",
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ **Geral**",
        value="`/ajuda` - Mostrar este menu\n"
              "`/set_imagem` - Definir imagem do produto",
        inline=False
    )
    
    embed.set_footer(text=f"{config.NOME_LOJA} - Versão 4.0.0")
    
    await interaction.response.send_message(embed=embed)

# ===============================
# INICIALIZAÇÃO
# ===============================
def run_flask():
    try:
        logger.info(f"🌐 Iniciando Flask na porta {config.PORT}")
        flask_app.run(host='0.0.0.0', port=config.PORT, debug=False)
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar Flask: {e}")

if __name__ == "__main__":
    try:
        if not config.DISCORD_TOKEN:
            logger.error("❌ DISCORD_TOKEN não configurado!")
            sys.exit(1)
        
        threading.Thread(target=run_flask, daemon=True).start()
        logger.info("✅ Servidor Flask iniciado")
        
        logger.info("🤖 Iniciando bot Discord...")
        bot.run(config.DISCORD_TOKEN, log_handler=None)
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot encerrado pelo usuário")
    except Exception as e:
        logger.error(f"❌ Erro fatal: {e}")
        traceback.print_exc()
        sys.exit(1)
        app = flask_app
