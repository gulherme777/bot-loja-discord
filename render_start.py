import os
import threading
import time
from bot import bot, flask_app

# Força o carregamento do token
token = os.environ.get("DISCORD_TOKEN_G7") or os.environ.get("DISCORD_TOKEN")

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"✅ Servidor Web (Flask) iniciando na porta {port}...")
    try:
        # Usando threaded=True para o Flask lidar com múltiplas requisições
        flask_app.run(host='0.0.0.0', port=port, threaded=True)
    except Exception as e:
        print(f"❌ Erro ao iniciar Flask: {e}")

if __name__ == "__main__":
    # 1. Inicia o Flask em uma thread separada
    print("🚀 Iniciando servidor Flask...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Pequena pausa para garantir que o Flask subiu antes do bot bloquear o processo
    time.sleep(2)

    # 2. Inicia o Bot do Discord
    print("🔄 Conectando ao Discord...")
    if token:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ Erro crítico no bot: {e}")
    else:
        print("❌ Erro: DISCORD_TOKEN não configurado no Render!")

    # Mantém o processo vivo caso o bot caia
    while True:
        time.sleep(3600)
