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


def test_non_operator_settings_ui_delivery_toggle_timezone_validation_and_logout(data_dir, monkeypatch):
    from onechapter.auth import create_magic_login_token
    from onechapter.db import setting
    from onechapter.library import (
        bootstrap,
        get_or_create_telegram_reader,
        set_telegram_delivery_enabled,
    )
    from onechapter.reader.models import ChannelIdentity

    bootstrap()
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    monkeypatch.setenv("ONECHAPTER_OPERATOR_TELEGRAM_IDS", "59911481")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "one_chapter_bot")

    reader = get_or_create_telegram_reader(None, 880011, "Public Reader", handle="pub_reader")
    assert reader.is_operator is False

    client = Client()
    login_resp = client.get(f"/auth/magic?token={create_magic_login_token(reader.id, 880011)}")
    assert login_resp.status_code == 303

    # 1. Generalized copy on / and /library, plus visible Sign out in base.html nav
    home_html = _text(client.get("/"))
    lib_html = _text(client.get("/library"))
    assert "Drop the MOBI of" not in lib_html
    assert "Технология жизни" not in home_html
    assert "Технология жизни" not in lib_html
    assert 'action="/logout"' in home_html
    assert "Sign out" in home_html

    # 2. Non-operator /settings hides @BotFather / /newbot / Bot is idle / bot token input,
    #    and provides timezone datalist, Telegram delivery toggle, @one_chapter_bot deep-link, and Sign out
    settings_resp = client.get("/settings")
    assert settings_resp.status_code == 200
    settings_html = _text(settings_resp)
    assert "@BotFather" not in settings_html
    assert "/newbot" not in settings_html
    assert "Bot is idle" not in settings_html
    assert 'name="telegram_bot_token"' not in settings_html
    assert '<datalist id="iana-timezones">' in settings_html
    assert '<option value="Europe/Berlin"></option>' in settings_html
    assert 'name="telegram_delivery_enabled"' in settings_html
    assert "https://t.me/one_chapter_bot?start=connect" in settings_html
    assert 'action="/logout"' in settings_html

    # 3. Invalid timezone shows clear error notice instead of silently flashing "Saved."
    bad_tz = client.post(
        "/settings",
        data={
            "timezone": "Moscow",
            "delivery_hhmm": "07:30",
            "telegram_delivery_field": "1",
            "telegram_delivery_enabled": "1",
            "telegram_bot_token": "999:ATTEMPTED_INJECTION",
        },
        follow=True,
    )
    bad_tz_html = _text(bad_tz)
    assert "Unknown timezone" in bad_tz_html
    assert "Saved." not in bad_tz_html
    reader.refresh_from_db()
    assert reader.timezone == "Asia/Yerevan"
    assert setting(None, "telegram_bot_token", "") == ""

    # 4. Valid timezone + unchecking Telegram delivery toggle disables ChannelIdentity.is_delivery_enabled
    toggle_off = client.post(
        "/settings",
        data={
            "timezone": "Europe/Berlin",
            "delivery_hhmm": "07:30",
            "telegram_delivery_field": "1",
        },
        follow=True,
    )
    assert "Saved." in _text(toggle_off)
    reader.refresh_from_db()
    assert reader.timezone == "Europe/Berlin"
    assert reader.delivery_hhmm == "07:30"
    ci = ChannelIdentity.objects.get(reader=reader, channel="telegram")
    assert ci.is_delivery_enabled is False

    # 5. If Telegram marked chat unreachable (e.g. Forbidden before user pressed Start), /settings prompts clearly
    set_telegram_delivery_enabled(reader.id, enabled=False, reason="telegram_unreachable")
    unreachable_html = _text(client.get("/settings"))
    assert "Telegram could not deliver to your chat" in unreachable_html
    assert "https://t.me/one_chapter_bot?start=connect" in unreachable_html

    # Re-enabling delivery clears the unreachable reason
    toggle_on = client.post(
        "/settings",
        data={
            "timezone": "Europe/Berlin",
            "delivery_hhmm": "07:30",
            "telegram_delivery_field": "1",
            "telegram_delivery_enabled": "1",
        },
    )
    assert toggle_on.status_code == 303
    ci.refresh_from_db()
    assert ci.is_delivery_enabled is True
    assert "delivery_disabled_reason" not in (ci.metadata or {})

    # 6. POST /logout signs out the reader and redirects to /login
    logout_resp = client.post("/logout")
    assert logout_resp.status_code == 303
    assert logout_resp.headers["location"] == "/login"
    after_logout = client.get("/")
    assert after_logout.status_code == 302
    assert after_logout.headers["location"] == "/login"


def test_web_telegram_login_captures_browser_timezone_for_new_readers_only(data_dir, monkeypatch):
    import hashlib
    import hmac
    import time

    from onechapter.library import bootstrap
    from onechapter.reader.models import Reader

    bootstrap()
    bot_token = "123456789:ABCDEF_tz_login_secret"
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    monkeypatch.setenv("ONECHAPTER_OPERATOR_TELEGRAM_IDS", "59911481")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "one_chapter_bot")

    def _signed_widget_payload(tg_id: str, first_name: str) -> dict[str, str]:
        now_ts = int(time.time())
        payload = {
            "id": tg_id,
            "first_name": first_name,
            "auth_date": str(now_ts),
        }
        check_str = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
        secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
        payload["hash"] = hmac.new(secret, check_str.encode("utf-8"), hashlib.sha256).hexdigest()
        return payload

    # 1. Login page includes browser Intl timezone detection script
    client = Client()
    login_html = _text(client.get("/login"))
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in login_html

    # 2. Brand-new reader logging in with tz=America/New_York gets America/New_York instead of Asia/Yerevan
    payload_ny = _signed_widget_payload("910001", "Clara")
    payload_ny["tz"] = "America/New_York"
    resp_ny = client.get("/auth/telegram", data=payload_ny)
    assert resp_ny.status_code == 303
    assert resp_ny.headers["location"] == "/"
    clara = Reader.objects.get(channels__channel="telegram", channels__external_id="910001")
    assert clara.timezone == "America/New_York"

    # 3. Re-logging in later from another browser timezone does NOT overwrite Clara's saved timezone
    relogin_payload = _signed_widget_payload("910001", "Clara")
    relogin_payload["tz"] = "Asia/Tokyo"
    resp_relogin = Client().get("/auth/telegram", data=relogin_payload)
    assert resp_relogin.status_code == 303
    clara.refresh_from_db()
    assert clara.timezone == "America/New_York"

    # 4. Cookie fallback (onechapter_tz) and invalid tz graceful fallback
    cookie_client = Client()
    cookie_client.cookies["onechapter_tz"] = "Europe%2FLisbon"
    resp_cookie = cookie_client.get("/auth/telegram", data=_signed_widget_payload("910002", "Joao"))
    assert resp_cookie.status_code == 303
    joao = Reader.objects.get(channels__channel="telegram", channels__external_id="910002")
    assert joao.timezone == "Europe/Lisbon"

    bad_tz_payload = _signed_widget_payload("910003", "Fallback")
    bad_tz_payload["tz"] = "Mars/Olympus_Mons"
    resp_fallback = Client().get("/auth/telegram", data=bad_tz_payload)
    assert resp_fallback.status_code == 303
    fallback_reader = Reader.objects.get(channels__channel="telegram", channels__external_id="910003")
    assert fallback_reader.timezone == "Asia/Yerevan"


def test_reflection_length_cap_and_security_headers(data_dir):
    import pytest
    from django.test import override_settings

    from onechapter.library import add_reflection, bootstrap, get_book, get_or_create_local_reader, start_book
    from onechapter.reader.models import Reflection

    bootstrap()
    client = Client()
    client.post("/book/slow-reading/start")

    # 1. Standard security headers on web responses
    resp = client.get("/read/slow-reading/0")
    assert resp.status_code == 200
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert resp.headers.get("X-Frame-Options") == "DENY"

    with override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["testserver", "app.onechapter.am"],
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
    ):
        https_resp = Client().get("/login", secure=True)
        assert "max-age=31536000" in https_resp.headers.get("Strict-Transport-Security", "")
        assert "includeSubDomains" in https_resp.headers.get("Strict-Transport-Security", "")

    # 2. Web POST /read/<slug>/<pos>/note enforces 10,000-char limit cleanly
    too_long = client.post("/read/slow-reading/0/note", data={"body": "a" * 10_001})
    assert too_long.status_code == 303
    assert "notice=note_too_long" in too_long.headers["location"]
    assert Reflection.objects.count() == 0
    err_page = _text(client.get(too_long.headers["location"]))
    assert "too long" in err_page

    just_right = client.post("/read/slow-reading/0/note", data={"body": "b" * 10_000})
    assert just_right.status_code == 303
    assert "notice=" not in just_right.headers["location"]
    assert Reflection.objects.count() == 1

    # 3. Domain helper add_reflection also enforces limit directly
    reader = get_or_create_local_reader()
    book = get_book("slow-reading")
    state = start_book(None, reader.id, book.id)
    with pytest.raises(ValueError, match="maximum length"):
        add_reflection(None, state.plan_id, 0, "c" * 10_001)
