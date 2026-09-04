from fastapi.testclient import TestClient

from onechapter.web.app import app


def test_health_and_library(data_dir):
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "One chapter a day" in home.text
        lib = client.get("/library")
        assert lib.status_code == 200
        assert "A Note on Slow Reading" in lib.text
        start = client.post("/book/slow-reading/start", follow_redirects=True)
        assert start.status_code == 200
        assert "One page a day" in start.text
        book = client.get("/book/slow-reading")
        assert book.status_code == 200
        assert "locked" in book.text
        today = client.get("/read/slow-reading/0")
        assert today.status_code == 200
        future = client.get("/read/slow-reading/4", follow_redirects=False)
        assert future.status_code == 302
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.json()["bot"] is False
        settings = client.get("/settings")
        assert settings.status_code == 200
        assert "Bot is idle" in settings.text
        saved = client.post(
            "/settings",
            data={
                "timezone": "Asia/Yerevan",
                "delivery_hhmm": "08:00",
                "telegram_bot_token": "",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert "notice=saved" in saved.headers["location"]


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
    with TestClient(app) as client:
        with book.open("rb") as fh:
            res = client.post("/upload", files={"file": ("tiny.txt", fh, "text/plain")}, follow_redirects=False)
        assert res.status_code == 303
        loc = res.headers["location"]
        page = client.get(loc)
        assert page.status_code == 200
        assert "Begin today" in page.text

