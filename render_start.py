import os
import threading
from bot import bot, flask_app

# Força o carregamento do token
token = os.environ.get("DISCORD_TOKEN_G7") or os.environ.get("DISCORD_TOKEN")

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"✅ Servidor Web rodando na porta {port}")
    # Usando threaded=True para o Flask lidar com múltiplas requisições se necessário
    flask_app.run(host='0.0.0.0', port=port, threaded=True)

if __name__ == "__main__":
    # Inicia o Flask em segundo plano
    print("🚀 Iniciando servidor Flask...")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    print("🔄 Conectando ao Discord...")
    if token:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ Erro ao iniciar o bot: {e}")
    else:
        print("❌ Erro: DISCORD_TOKEN ou DISCORD_TOKEN_G7 não configurado no Render!")
