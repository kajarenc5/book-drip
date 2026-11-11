import os

import django
import pytest
from django.db import connections

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onechapter.project.settings")
os.environ.setdefault("POSTGRES_DB", "onechapter_test")
django.setup()

import onechapter.config as config
from onechapter.db import init_db
from onechapter.local_pg import ensure_local_postgres


@pytest.fixture(scope="session", autouse=True)
def _postgres_test_db():
    """Ensure local PostgreSQL 17 is running with a dedicated test database."""
    ensure_local_postgres("onechapter_test")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Isolate filesystem directories and reset PostgreSQL tables for each test."""
    monkeypatch.setenv("POSTGRES_DB", "onechapter_test")
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

    from onechapter.reader import middleware

    middleware._ready.clear()
    return tmp_path
