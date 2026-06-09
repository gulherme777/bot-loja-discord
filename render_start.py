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
MP_ACCESS_TOKEN = os.environ.get("MP_ACCESS_TOKEN", "")

# Se estiver testando localmente com .env
try:
    from dotenv import load_dotenv
    load_dotenv()
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", DISCORD_TOKEN)
    MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", MP_ACCESS_TOKEN)
except ImportError:
    pass  # Está no Render, usa as variáveis de ambiente já definidas

# Verificar se o token existe
if not DISCORD_TOKEN:
    print("❌ ERRO: DISCORD_TOKEN não encontrado!")
    print("Configure a variável de ambiente DISCORD_TOKEN no Render")
    exit(1)

if not MP_ACCESS_TOKEN:
    print("⚠️ AVISO: MP_ACCESS_TOKEN não configurado!")
    print("O bot vai funcionar, mas os pagamentos não vão funcionar.")

# ===============================
# RESTO DO SEU CÓDIGO AQUI
# (cole TODO o código do bot que eu enviei antes)
# ===============================

# ... (todo o código do bot vai aqui) ...

# ===============================
# INICIAR BOT E FLASK
# ===============================
if __name__ == "__main__":
    # Iniciar Flask em thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Iniciar bot
    print("🚀 Iniciando bot Discord...")
    bot.run(DISCORD_TOKEN)
