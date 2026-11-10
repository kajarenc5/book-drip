from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

from django.core import signing

MAGIC_TOKEN_SALT = "onechapter.auth.magic"
MAGIC_TOKEN_MAX_AGE_SECONDS = 900  # 15 minutes
TELEGRAM_WIDGET_MAX_AGE_SECONDS = 86400  # 24 hours


def create_magic_login_token(reader_id: int, telegram_id: int | str) -> str:
    payload = {
        "reader_id": int(reader_id),
        "channel": "telegram",
        "external_id": str(telegram_id),
    }
    return signing.dumps(payload, salt=MAGIC_TOKEN_SALT)


def verify_magic_login_token(
    token: str,
    *,
    max_age: int = MAGIC_TOKEN_MAX_AGE_SECONDS,
) -> dict[str, object] | None:
    if not token:
        return None
    try:
        data = signing.loads(token, salt=MAGIC_TOKEN_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or "reader_id" not in data:
        return None
    return data


def verify_telegram_login_data(
    params: Mapping[str, str],
    bot_token: str,
    *,
    now: int | None = None,
    max_age: int = TELEGRAM_WIDGET_MAX_AGE_SECONDS,
) -> dict[str, str] | None:
    """Verify Telegram Login Widget callback parameters using HMAC-SHA256.

    See https://core.telegram.org/widgets/login#checking-authorization
    """
    bot_token = (bot_token or "").strip()
    if not bot_token:
        return None
    received_hash = params.get("hash", "")
    user_id = params.get("id", "")
    auth_date_str = params.get("auth_date", "")
    if not received_hash or not user_id or not auth_date_str:
        return None
    try:
        auth_date = int(auth_date_str)
    except ValueError:
        return None

    current_ts = int(time.time()) if now is None else int(now)
    if current_ts - auth_date > max_age or auth_date > current_ts + 300:
        return None

    check_lines = [
        f"{k}={v}"
        for k, v in sorted(params.items())
        if k != "hash" and v is not None and v != ""
    ]
    data_check_string = "\n".join(check_lines)
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        return None
    return {k: str(v) for k, v in params.items() if v is not None}
