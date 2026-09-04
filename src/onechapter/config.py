from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("ONECHAPTER_DATA") or (ROOT / "data")).resolve()
SEED_DIR = ROOT / "data" / "seed"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "app.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

HOST = os.environ.get("ONECHAPTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("ONECHAPTER_PORT", "43147"))
DEFAULT_TIMEZONE = os.environ.get("ONECHAPTER_TIMEZONE", "Asia/Yerevan")
DEFAULT_DELIVERY = os.environ.get("ONECHAPTER_DELIVERY", "08:00")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)


def env_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def public_url() -> str:
    explicit = os.environ.get("ONECHAPTER_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    return f"http://{HOST}:{PORT}"
