import os
import threading
from bot import bot, flask_app

# Força o carregamento do token
token = os.environ.get("DISCORD_TOKEN_G7") or os.environ.get("DISCORD_TOKEN")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    print(f"✅ Servidor Web rodando na porta {port}")
    flask_app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # Inicia o Flask em segundo plano
    threading.Thread(target=run_flask, daemon=True).start()
    
    print("🔄 Conectando ao Discord...")
    if token:
        bot.run(token)
    else:
        print("❌ Erro: DISCORD_TOKEN não configurado no Render!")
