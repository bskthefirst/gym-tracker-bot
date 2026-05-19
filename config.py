import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("GYM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Set GYM_BOT_TOKEN in .env")

USER_ID = int(os.getenv("GYM_USER_ID", "0"))
if not USER_ID:
    raise ValueError("Set GYM_USER_ID in .env (your Telegram numeric ID)")

DB_PATH = os.getenv("GYM_DB_PATH", "gym.db")
PHOTO_DIR = os.getenv("GYM_PHOTO_DIR", "photos")
DAILY_GOAL_KCAL = int(os.getenv("GYM_DAILY_GOAL", "1000"))
DAILY_REPORT_HOUR = int(os.getenv("GYM_DAILY_REPORT_HOUR", "21"))

os.makedirs(PHOTO_DIR, exist_ok=True)
