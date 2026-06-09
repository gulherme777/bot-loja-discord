from bot import bot
import os
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
