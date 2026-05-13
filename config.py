import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
    BOARD_CHAT_ID = os.getenv("BOARD_CHAT_ID")
    YANDEX_DISK_TOKEN = os.getenv("YANDEX_DISK_TOKEN")
    YANDEX_DISK_BASE_PATH = os.getenv("YANDEX_DISK_BASE_PATH", "/Документы_ДТСН")
    ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY", "change_this_key")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
    BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
    ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "")  # ID председателя для личных уведомлений (запасной канал)

config = Config()
