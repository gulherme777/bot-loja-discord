#!/usr/bin/env python3
import os
import sys
import subprocess
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    logger.info("🚀 Iniciando G7 STORE Bot no Render...")
    
    bot_file = "bot.py"
    if not os.path.exists(bot_file):
        logger.error(f"❌ Arquivo {bot_file} não encontrado!")
        sys.exit(1)
    
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN não configurado!")
        sys.exit(1)
    
    logger.info("✅ Token encontrado")
    logger.info("🔄 Iniciando bot...")
    
    try:
        subprocess.run([sys.executable, bot_file], check=True)
    except Exception as e:
        logger.error(f"❌ Erro: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
