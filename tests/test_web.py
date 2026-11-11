import json

import django
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client


def _text(response) -> str:
    if response.streaming:
        return b"".join(response.streaming_content).decode()
    return response.content.decode()


def test_django_lts_and_asgi_app():
    from onechapter.project.asgi import application

    assert django.get_version() == "5.2.19"
    assert django.VERSION[:2] == (5, 2)
    assert callable(application)


def test_health_and_library(data_dir):
    client = Client()
    home = client.get("/")
    assert home.status_code == 200
    page = _text(home)
    assert "One chapter a day" in page
    assert "csrfmiddlewaretoken" in page
    assert 'href="/static/styles.css"' in page
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "--paper" in _text(css)
    lib = client.get("/library")
    assert lib.status_code == 200
    assert "A Note on Slow Reading" in _text(lib)
    start = client.post("/book/slow-reading/start", follow=True)
    assert start.status_code == 200
    assert "One page a day" in _text(start)
    book = client.get("/book/slow-reading")
    assert book.status_code == 200
    assert "locked" in _text(book)
    today = client.get("/read/slow-reading/0")
    assert today.status_code == 200
    future = client.get("/read/slow-reading/4")
    assert future.status_code == 302
    health = client.get("/health")
    assert health.status_code == 200
    payload = json.loads(health.content)
    assert payload["ok"] is True
    assert payload["bot"] is False
    settings = client.get("/settings")
    assert settings.status_code == 200
    assert "Bot is idle" in _text(settings)
    saved = client.post(
        "/settings",
        data={
            "timezone": "Asia/Yerevan",
            "delivery_hhmm": "08:00",
            "telegram_bot_token": "",
        },
    )
    assert saved.status_code == 303
    assert "notice=saved" in saved.headers["location"]
    kept = client.post(
        "/read/slow-reading/0/note",
        data={"body": "Read it once, then close the book."},
    )
    assert kept.status_code == 303
    again = client.get("/read/slow-reading/0")
    assert "Read it once, then close the book." in _text(again)


def test_upload_generic_txt(data_dir, tmp_path):
    book = tmp_path / "tiny.txt"
    book.write_text(
        "# Opening\n\nThis opening section is long enough to keep.\n\n"
        + ("More sentences about beginning the practice of reading slowly. " * 8)
        + "\n\n# Second\n\n"
        + ("The second section continues with enough words to survive the splitter. " * 8)
        + "\n\n# Third\n\n"
        + ("The third section is the last daily unit in this tiny sample book. " * 8),
        encoding="utf-8",
    )
    client = Client()
    uploaded = SimpleUploadedFile("tiny.txt", book.read_bytes(), content_type="text/plain")
    res = client.post("/upload", {"file": uploaded})
    assert res.status_code == 303
    loc = res.headers["location"]
    page = client.get(loc)
    assert page.status_code == 200
    assert "Begin today" in _text(page)


def test_empty_upload_is_rejected(data_dir):
    client = Client()
    uploaded = SimpleUploadedFile("empty.txt", b"", content_type="text/plain")
    res = client.post("/upload", {"file": uploaded})
    assert res.status_code == 303
    assert "notice=empty" in res.headers["location"]


def test_oversized_upload_is_rejected(data_dir, monkeypatch):
    monkeypatch.setenv("ONECHAPTER_MAX_UPLOAD_BYTES", "64")
    client = Client()
    uploaded = SimpleUploadedFile("huge.txt", b"x" * 128, content_type="text/plain")
    res = client.post("/upload", {"file": uploaded})
    assert res.status_code == 303
    assert "notice=too_large" in res.headers["location"]
    lib = client.get("/library?notice=too_large")
    assert "too large" in _text(lib)
