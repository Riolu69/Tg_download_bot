import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в файле .env")

USE_LOCAL_API = os.getenv("USE_LOCAL_API", "false").lower() == "true"
LOCAL_API_URL = os.getenv("LOCAL_API_URL", "http://127.0.0.1:8081")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "49"))

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

COOKIES_PATH = BASE_DIR / "cookies.txt"
COOKIES_FILE = str(COOKIES_PATH) if COOKIES_PATH.exists() else None

IG_COOKIES_PATH = Path(os.getenv("IG_COOKIES_FILE", str(BASE_DIR / "ig_cookies.txt")))
IG_COOKIES_FILE = str(IG_COOKIES_PATH) if IG_COOKIES_PATH.exists() else None
