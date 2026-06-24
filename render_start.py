import os
import sys
import threading
import time
from flask import Flask

print("🚀 Iniciando G7 STORE no Render...")

# Verifica variáveis críticas
token = os.environ.get("DISCORD_TOKEN_G7", "")
if not token:
    print("❌ ERRO: DISCORD_TOKEN_G7 não configurado!")
    sys.exit(1)

print("✅ Token encontrado. Importando bot...")

# Importa o bot depois de verificar as variáveis
try:
    from bot import bot
    print("✅ Bot importado com sucesso!")
except ImportError as e:
    print(f"❌ Erro ao importar bot: {e}")
    sys.exit(1)

# Configura o Flask para o health check
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 G7 STORE - Bot está online!", 200

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    # O Render define a porta via variável PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Inicia o Flask em uma thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Servidor Flask iniciado em background.")
    
    print("🔄 Iniciando bot Discord...")
    try:
        # O bot.run() BLOQUEIA a execução, mantendo o processo vivo
        bot.run(token)
    except Exception as e:
        print(f"❌ Erro crítico no bot: {e}")
        # Mantém o processo vivo mesmo com erro para logs
        while True:
            time.sleep(10)
            print("🔄 Bot caiu, aguardando reinicialização...")
