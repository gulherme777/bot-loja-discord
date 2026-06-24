import os
import sys
import threading
import time

print("🚀 Iniciando Bot no Render...")

# Pega o token
token = os.environ.get("DISCORD_TOKEN", "")

if not token:
    print("❌ ERRO: DISCORD_TOKEN não configurado!")
    print("🔍 Variáveis disponíveis:")
    for key in os.environ.keys():
        if "TOKEN" in key or "DISCORD" in key:
            print(f"  - {key}: {'CONFIGURADO' if os.environ.get(key) else 'VAZIO'}")
    sys.exit(1)

print("✅ Token encontrado!")

# Importa o bot e o app Flask do bot.py
try:
    from bot import bot, app
    print("✅ Bot e Flask importados com sucesso!")
except ImportError as e:
    print(f"❌ Erro ao importar: {e}")
    sys.exit(1)

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    # Inicia o Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"✅ Servidor Flask rodando na porta {os.environ.get('PORT', 5000)}")
    
    print("🔄 Iniciando bot Discord...")
    try:
        bot.run(token)
    except Exception as e:
        print(f"❌ Erro no bot: {e}")
        while True:
            time.sleep(10)
            print("🔄 Bot caiu, aguardando...")
