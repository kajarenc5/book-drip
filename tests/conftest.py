import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onechapter.project.settings")
django.setup()

from onechapter import config


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.json")
    (tmp_path / "uploads").mkdir()
    return tmp_path
