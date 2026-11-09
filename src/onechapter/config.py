from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

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
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "eu-north-1"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / "originals").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)


def env_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def s3_bucket() -> str:
    return os.environ.get("ONECHAPTER_S3_BUCKET", "").strip()


def access_mode() -> str:
    """Return 'invite' or 'public'.

    Defaults to 'invite' when DJANGO_DEBUG=0 (production) and 'public' in local
    development unless ONECHAPTER_ACCESS_MODE is explicitly set.
    """
    explicit = os.environ.get("ONECHAPTER_ACCESS_MODE", "").strip().lower()
    if explicit in {"invite", "invite_only"}:
        return "invite"
    if explicit == "public":
        return "public"
    debug = os.environ.get("DJANGO_DEBUG", "1") != "0"
    return "public" if debug else "invite"


def allowed_telegram_ids() -> set[str]:
    raw = os.environ.get("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def invite_code() -> str:
    return os.environ.get("ONECHAPTER_INVITE_CODE", "").strip()


def require_web_login() -> bool:
    return os.environ.get("ONECHAPTER_REQUIRE_WEB_LOGIN", "0").strip() in {"1", "true", "yes"}


def is_access_allowed(
    external_id: str | int | None = None,
    provided_invite: str | None = None,
) -> bool:
    if access_mode() == "public":
        return True
    if external_id is not None and str(external_id) in allowed_telegram_ids():
        return True
    expected_invite = invite_code()
    if expected_invite and provided_invite:
        cleaned = provided_invite.strip()
        if cleaned.startswith("inv_"):
            cleaned = cleaned[4:]
        if cleaned == expected_invite:
            return True
    return False


def public_url() -> str:
    explicit = os.environ.get("ONECHAPTER_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    return f"http://{HOST}:{PORT}"


def database_config(default_sqlite_path: Path | None = None) -> dict[str, object]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sqlite_path = default_sqlite_path or DB_PATH
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(sqlite_path),
        }
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in {"postgres", "postgresql", "pgsql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")) or "onechapter",
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": str(parsed.port or 5432),
            "CONN_MAX_AGE": int(os.environ.get("DJANGO_CONN_MAX_AGE", "60")),
        }
    if scheme == "sqlite":
        path_part = unquote(parsed.path or "")
        if path_part in {"", "/", "/:memory:"} or url.endswith(":memory:"):
            name = ":memory:"
        else:
            name = path_part
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": name,
        }
    raise ValueError(f"Unsupported DATABASE_URL scheme: {scheme}")
