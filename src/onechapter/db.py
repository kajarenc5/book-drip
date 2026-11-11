from __future__ import annotations

import json
from contextlib import contextmanager

from django.conf import settings
from django.core.management import call_command
from django.db import connections

from onechapter.config import database_config, ensure_dirs
from onechapter.local_pg import ensure_local_postgres, is_using_managed_local_pg

_migrated_targets: set[str] = set()


def current_db_target() -> str:
    """Sync Django's default database settings with environment overrides and return a target key."""
    ensure_dirs()
    desired = database_config()
    db_cfg = settings.DATABASES["default"]
    conn = connections["default"]

    changed = any(
        str(db_cfg.get(k)) != str(desired.get(k))
        for k in ("ENGINE", "NAME", "USER", "PASSWORD", "HOST", "PORT")
    )
    if changed:
        conn.close()
        for k, v in desired.items():
            db_cfg[k] = v
            conn.settings_dict[k] = v

    if is_using_managed_local_pg():
        ensure_local_postgres(str(db_cfg["NAME"]))

    return f"{db_cfg.get('HOST')}:{db_cfg.get('PORT')}/{db_cfg.get('NAME')}"


def init_db() -> None:
    """Ensure PostgreSQL is running and apply Django migrations if not yet migrated in this process."""
    target = current_db_target()
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
    """Compatibility context manager that ensures the PostgreSQL database is initialized."""
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
