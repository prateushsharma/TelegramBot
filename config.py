import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "30"))
