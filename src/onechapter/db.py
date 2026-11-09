from __future__ import annotations

import json
import os
from contextlib import contextmanager

from django.conf import settings
from django.core.management import call_command
from django.db import connections

from onechapter import config
from onechapter.config import ensure_dirs

_migrated_targets: set[str] = set()


def sync_sqlite_db_path() -> str:
    """Keep Django's default SQLite path aligned with config.DB_PATH when DATABASE_URL is unset."""
    ensure_dirs()
    db_cfg = settings.DATABASES["default"]
    if not os.environ.get("DATABASE_URL", "").strip() and db_cfg.get("ENGINE") == "django.db.backends.sqlite3":
        target = str(config.DB_PATH)
        conn = connections["default"]
        if str(conn.settings_dict.get("NAME")) != target:
            conn.close()
            db_cfg["NAME"] = target
            conn.settings_dict["NAME"] = target
        return target
    return str(db_cfg.get("NAME") or "default")


def init_db() -> None:
    """Apply Django migrations for the configured database if not yet migrated in this process."""
    target = sync_sqlite_db_path()
    if target in _migrated_targets and _has_core_tables():
        return
    call_command("migrate", verbosity=0, interactive=False)
    _migrated_targets.add(target)


def _has_core_tables() -> bool:
    conn = connections["default"]
    try:
        return "books" in conn.introspection.table_names()
    except Exception:
        return False


@contextmanager
def db():
    """Compatibility context manager that ensures the Django database is initialized."""
    init_db()
    yield connections["default"]


def setting(conn_or_key=None, key: str | None = None, default: str = "") -> str:
    from onechapter.reader.models import AppSetting

    init_db()
    actual_key = conn_or_key if isinstance(conn_or_key, str) and key is None else key
    if not actual_key:
        return default
    row = AppSetting.objects.filter(key=actual_key).first()
    return row.value if row else default


def set_setting(conn_or_key=None, key: str | None = None, value: str = "") -> None:
    from onechapter.reader.models import AppSetting

    init_db()
    if isinstance(conn_or_key, str) and value == "" and key is not None:
        actual_key = conn_or_key
        actual_value = key
    else:
        actual_key = key or ""
        actual_value = value
    AppSetting.objects.update_or_create(key=actual_key, defaults={"value": actual_value})


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
