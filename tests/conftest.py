import os
from urllib.parse import urlparse

import django
import pytest
from django.db import connections

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onechapter.project.settings")
os.environ.setdefault("POSTGRES_DB", "onechapter_test")


def _resolve_external_test_db_url() -> str:
    """Return an explicit PostgreSQL *_test URL (e.g. CI service container), or '' for local_pg."""
    explicit_test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    candidate = explicit_test_url or os.environ.get("DATABASE_URL", "").strip()
    if not candidate:
        return ""
    parsed = urlparse(candidate)
    db_name = parsed.path.lstrip("/")
    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run destructive test TRUNCATE against non-test DATABASE_URL db '{db_name}'."
        )
    return candidate


_EXTERNAL_TEST_DATABASE_URL = _resolve_external_test_db_url()
django.setup()

import onechapter.config as config
from onechapter.db import init_db
from onechapter.local_pg import ensure_local_postgres


@pytest.fixture(scope="session", autouse=True)
def _postgres_test_db():
    """Ensure PostgreSQL 17 is running with a dedicated test database."""
    if not _EXTERNAL_TEST_DATABASE_URL:
        ensure_local_postgres("onechapter_test")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Isolate filesystem directories and reset PostgreSQL tables for each test."""
    monkeypatch.setenv("POSTGRES_DB", "onechapter_test")
    if _EXTERNAL_TEST_DATABASE_URL:
        monkeypatch.setenv("DATABASE_URL", _EXTERNAL_TEST_DATABASE_URL)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    config.ensure_dirs()

    django.setup()
    init_db()
    with connections["default"].cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE reflections, deliveries, plans, chapters, books, "
            "channel_identities, readers, settings, auth_user RESTART IDENTITY CASCADE;"
        )

    from onechapter.library import reset_upload_rate_limits
    from onechapter.reader import middleware

    reset_upload_rate_limits()
    middleware._ready.clear()
    return tmp_path
