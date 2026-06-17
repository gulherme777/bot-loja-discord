#!/usr/bin/env python3
"""
Arquivo de entrada para o Render.com
Este arquivo inicia o bot corretamente no ambiente do Render
"""

import os
import sys
import subprocess
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Função principal que inicia o bot"""
    logger.info("🚀 Iniciando G7 STORE Bot no Render...")
    
    # Verificar se o arquivo do bot existe
    bot_file = "bot.py"
    if not os.path.exists(bot_file):
        logger.error(f"❌ Arquivo {bot_file} não encontrado!")
        logger.info("📁 Arquivos no diretório atual:")
        for file in os.listdir('.'):
            logger.info(f"  - {file}")
        sys.exit(1)
    
    # Verificar token
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN não configurado nas variáveis de ambiente!")
        logger.info("Configure a variável DISCORD_TOKEN no painel do Render")
        sys.exit(1)
    
    logger.info("✅ Token encontrado (tamanho: {} caracteres)".format(len(token)))
    logger.info("🔄 Iniciando bot...")
    
    try:
        # Executar o bot com Python
        subprocess.run(
            [sys.executable, bot_file],
            check=True
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Bot finalizou com erro: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("🛑 Bot interrompido pelo usuário")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Erro inesperado: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
