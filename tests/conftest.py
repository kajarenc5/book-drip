import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onechapter.project.settings")
django.setup()

from onechapter import config
from onechapter.db import sync_sqlite_db_path
from onechapter.reader import middleware


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "uploads" / "originals").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    middleware._ready.clear()
    sync_sqlite_db_path()
    return tmp_path
