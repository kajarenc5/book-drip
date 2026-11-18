from __future__ import annotations

import hashlib
import hmac
import time

from django.db import connections
from django.test import Client

from onechapter import config, storage
from onechapter.auth import (
    create_magic_login_token,
    verify_magic_login_token,
    verify_telegram_login_data,
)
from onechapter.db import init_db, set_setting, setting
from onechapter.ingest.split import ChapterDraft, SplitResult
from onechapter.library import (
    add_reflection,
    get_book,
    get_chapter,
    get_or_create_channel_reader,
    get_or_create_local_reader,
    get_or_create_telegram_reader,
    ingest_file,
    list_active_plans,
    reflections_for,
    reprocess_book,
    set_reader_blocked,
    start_book,
    upsert_book_from_split,
)
from onechapter.reader.models import Book, ChannelIdentity, Chapter, Reader, Reflection


def test_django_migrations_create_all_domain_tables(data_dir, monkeypatch):
    init_db()
    assert connections["default"].vendor == "postgresql"
    tables = set(connections["default"].introspection.table_names())
    assert {
        "books",
        "chapters",
        "readers",
        "channel_identities",
        "plans",
        "deliveries",
        "reflections",
        "settings",
    } <= tables

    monkeypatch.setenv("DATABASE_URL", "sqlite:///not-supported.db")
    try:
        config.database_config()
    except ValueError as exc:
        assert "PostgreSQL exclusively" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-PostgreSQL DATABASE_URL")


def test_second_startup_keeps_settings_and_rows(data_dir):
    init_db()
    set_setting(None, "token", "secret-val")
    init_db()
    assert setting(None, "token") == "secret-val"


def test_unified_identity_merges_web_and_telegram_and_supports_future_channels(data_dir):
    init_db()
    web_reader = get_or_create_local_reader()
    tg_reader = get_or_create_telegram_reader(None, 777001, "Karen", handle="karen")

    # The initial local web reader and the first Telegram reader unify into one Reader row.
    assert web_reader.id == tg_reader.id
    assert tg_reader.telegram_id == 777001
    assert tg_reader.display_name == "Karen"

    # Attach a future Signal channel to the same Reader
    ChannelIdentity.objects.create(
        reader=tg_reader,
        channel="signal",
        external_id="+37400000000",
    )
    sig_reader = get_or_create_channel_reader("signal", "+37400000000")
    assert sig_reader.id == tg_reader.id


def test_invite_only_access_control_and_invite_codes(data_dir, monkeypatch):
    init_db()
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "invite")
    monkeypatch.setenv("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "10001,20002")
    monkeypatch.setenv("ONECHAPTER_INVITE_CODE", "slowread2026")

    # Initial local reader exists before any Telegram user connects
    local_op = get_or_create_local_reader()

    # Uninvited stranger connects first and is created with is_allowed=False
    stranger = get_or_create_telegram_reader(None, 99999, "Stranger")
    assert stranger.is_allowed is False
    assert stranger.id != local_op.id

    # Operator connects AFTER the rejected stranger and still unifies into local_op!
    operator = get_or_create_telegram_reader(None, 10001, "Operator")
    assert operator.is_allowed is True
    assert operator.id == local_op.id

    # Allowlisted Telegram ID is allowed immediately
    allowlisted = get_or_create_telegram_reader(None, 20002, "Friend")
    assert allowlisted.is_allowed is True

    # Presenting the valid invite code upgrades the stranger to allowed
    invited = get_or_create_telegram_reader(
        None,
        99999,
        "Stranger",
        invite_code="inv_slowread2026",
    )
    assert invited.id == stranger.id
    assert invited.is_allowed is True


def test_user_upload_does_not_overwrite_existing_book_and_hides_uploader(data_dir, tmp_path):
    init_db()
    alice = get_or_create_telegram_reader(None, 111, "Alice SecretName")
    bob = get_or_create_telegram_reader(None, 222, "Bob")

    file_v1 = tmp_path / "parables.txt"
    file_v1.write_text(
        "# First Parable\n\n"
        + ("Original first parable text with enough words for the splitter. " * 8)
        + "\n\n# Second Parable\n\n"
        + ("Original second parable text with enough words for the splitter. " * 8),
        encoding="utf-8",
    )
    book1 = ingest_file(file_v1, uploaded_by=alice, allow_replace=False)
    assert book1.uploaded_by_id == alice.id
    assert book1.chapter_count == 2

    # Bob uploads the exact same file bytes -> deduplicated by sha256, returns book1
    book_dup = ingest_file(file_v1, uploaded_by=bob, allow_replace=False)
    assert book_dup.id == book1.id
    assert Book.objects.get(id=book1.id).uploaded_by_id == alice.id

    # Bob uploads a modified file with the same filename -> must NOT overwrite Alice's book1
    file_v2 = tmp_path / "other" / "parables.txt"
    file_v2.parent.mkdir()
    file_v2.write_text(
        "# First Parable\n\n"
        + ("Modified text that should never overwrite the first book. " * 8),
        encoding="utf-8",
    )
    ingest_file(file_v2, uploaded_by=bob, allow_replace=False)
    original_ch0 = get_chapter(None, book1.id, 0)
    assert "Original first parable text" in original_ch0.body

    # Verify the web UI never exposes Alice's name as uploader
    client = Client()
    lib_html = client.get("/library").content.decode()
    book_html = client.get(f"/book/{book1.slug}").content.decode()
    assert "Alice SecretName" not in lib_html
    assert "Alice SecretName" not in book_html


def test_reflections_survive_operator_reprocess_when_chapter_positions_shift(data_dir):
    init_db()
    reader = get_or_create_local_reader()

    initial_split = SplitResult(
        title="Book of Essays",
        author="Author",
        profile="generic",
        chapters=[
            ChapterDraft(index=0, title="The Monkey Feeder", body="Monkeys and chestnuts.", part="Part I"),
            ChapterDraft(index=1, title="Horizontal Career", body="Grow where you stand.", part="Part I"),
        ],
    )
    book = upsert_book_from_split(
        None,
        initial_split,
        slug="book-of-essays",
        filename="essays.epub",
    )
    ch0_before = get_chapter(None, book.id, 0)
    state = start_book(None, reader.id, book.id)

    # Reader records both a text reflection and an audio reflection metadata entry on "The Monkey Feeder" (pos 0)
    add_reflection(
        None,
        state.plan_id,
        0,
        "Remember the four in the morning and three in the evening.",
        source_channel="web",
    )
    add_reflection(
        None,
        state.plan_id,
        0,
        "Voice thought on the monkey parable",
        source_channel="telegram",
        kind="audio",
        audio_s3_key=f"media/reflections/{reader.id}/sample.ogg",
        audio_duration_sec=42,
    )

    # Operator reprocesses the book and a new Preface is inserted at position 0,
    # shifting "The Monkey Feeder" from position 0 to position 1!
    reprocessed_split = SplitResult(
        title="Book of Essays",
        author="Author",
        profile="generic",
        chapters=[
            ChapterDraft(index=0, title="Preface to the Second Edition", body="New preface text.", part="Preface"),
            ChapterDraft(index=1, title="The Monkey Feeder", body="Monkeys and chestnuts (revised).", part="Part I"),
            ChapterDraft(index=2, title="Horizontal Career", body="Grow where you stand.", part="Part I"),
        ],
    )
    upsert_book_from_split(
        None,
        reprocessed_split,
        slug="book-of-essays",
        filename="essays.epub",
        allow_replace=True,
    )

    # "The Monkey Feeder" is now at position 1, with its Chapter.id preserved!
    ch1_after = get_chapter(None, book.id, 1)
    assert ch1_after.id == ch0_before.id
    assert ch1_after.title == "The Monkey Feeder"

    # Reflections now appear under position 1 ("The Monkey Feeder"), not under position 0 ("Preface")!
    preface_notes = reflections_for(None, state.plan_id, 0)
    monkey_notes = reflections_for(None, state.plan_id, 1)
    assert len(preface_notes) == 0
    assert len(monkey_notes) == 2
    assert monkey_notes[0].chapter_title_snapshot == "The Monkey Feeder"
    assert monkey_notes[0].chapter_position_snapshot == 0
    assert monkey_notes[1].kind == "audio"
    assert monkey_notes[1].audio_s3_key.endswith("/sample.ogg")

    # Reprocess again where "The Monkey Feeder" title is renamed (chapter_key changes)
    # while body content_sha256 stays identical -> matched by content_sha256 and reflection survives!
    renamed_split = SplitResult(
        title="Book of Essays",
        author="Author",
        profile="generic",
        chapters=[
            ChapterDraft(index=0, title="Preface to the Second Edition", body="New preface text.", part="Preface"),
            ChapterDraft(
                index=1,
                title="Three in the Morning, Four in the Evening",
                body="Monkeys and chestnuts (revised).",
                part="Part I",
            ),
            ChapterDraft(index=2, title="Horizontal Career", body="Grow where you stand.", part="Part I"),
        ],
    )
    upsert_book_from_split(
        None,
        renamed_split,
        slug="book-of-essays",
        filename="essays.epub",
        allow_replace=True,
    )
    ch1_renamed = get_chapter(None, book.id, 1)
    assert ch1_renamed.id == ch0_before.id
    renamed_notes = reflections_for(None, state.plan_id, 1)
    assert len(renamed_notes) == 2


def test_s3_storage_and_operator_reprocess_roundtrip(data_dir, tmp_path, monkeypatch):
    init_db()
    monkeypatch.setenv("ONECHAPTER_S3_BUCKET", "onechapter-test-bucket")

    objects: dict[tuple[str, str], bytes] = {}
    put_calls: list[str] = []

    class FakeS3Body:
        def __init__(self, raw: bytes):
            self._raw = raw

        def read(self) -> bytes:
            return self._raw

    class FakeS3Client:
        def put_object(self, *, Bucket, Key, Body, ServerSideEncryption):
            assert ServerSideEncryption == "AES256"
            objects[(Bucket, Key)] = Body
            put_calls.append(Key)

        def get_object(self, *, Bucket, Key):
            return {"Body": FakeS3Body(objects[(Bucket, Key)])}

    fake_s3 = FakeS3Client()
    sample = tmp_path / "sample.txt"
    raw_content = (
        "# Chapter One\n\n"
        + ("First chapter content stored in S3 originals prefix. " * 8)
        + "\n\n# Chapter Two\n\n"
        + ("Second chapter content stored in S3 originals prefix. " * 8)
    ).encode("utf-8")
    sample.write_bytes(raw_content)

    book = ingest_file(sample, s3_client=fake_s3)
    expected_sha = hashlib.sha256(raw_content).hexdigest()
    expected_key = f"originals/{expected_sha}/sample.txt"
    assert book.source_sha256 == expected_sha
    assert book.s3_key == expected_key
    assert book.source_filename == "sample.txt"
    assert ("onechapter-test-bucket", expected_key) in objects
    assert put_calls == [expected_key]

    # Delete local file and verify operator reprocess fetches from S3, updates in place,
    # and preserves s3_key and source_filename without re-uploading a temp file to S3!
    sample.unlink()
    reprocessed = reprocess_book(book.slug, s3_client=fake_s3)
    assert reprocessed.id == book.id
    assert reprocessed.chapter_count == 2
    assert reprocessed.s3_key == expected_key
    assert reprocessed.source_filename == "sample.txt"
    assert put_calls == [expected_key]


def test_telegram_login_widget_and_magic_link_auth(data_dir, monkeypatch):
    init_db()
    bot_token = "123456789:ABCDEF_test_secret_bot_token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "invite")
    monkeypatch.setenv("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "555123")
    monkeypatch.setenv("ONECHAPTER_INVITE_CODE", "webinvite2026")

    # Production default (DJANGO_DEBUG=0) enforces web login
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    assert config.require_web_login() is True

    unauth_client = Client()
    redirect_resp = unauth_client.get("/")
    assert redirect_resp.status_code == 302
    assert redirect_resp.headers["location"] == "/login"

    reader = get_or_create_telegram_reader(None, 555123, "Tigran")
    magic = create_magic_login_token(reader.id, 555123)
    verified_magic = verify_magic_login_token(magic)
    assert verified_magic is not None
    assert verified_magic["reader_id"] == reader.id

    client = Client()
    resp = client.get(f"/auth/magic?token={magic}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    # Test Telegram Login Widget HMAC-SHA256 verification and web invite redemption (/login?invite=...)
    now_ts = int(time.time())
    widget_data = {
        "id": "888999",
        "first_name": "Ani",
        "username": "ani_r",
        "auth_date": str(now_ts),
    }
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(widget_data.items()))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    widget_data["hash"] = hmac.new(secret_key, check_str.encode("utf-8"), hashlib.sha256).hexdigest()

    assert verify_telegram_login_data(widget_data, bot_token, now=now_ts) is not None
    tampered = dict(widget_data, first_name="Mallory")
    assert verify_telegram_login_data(tampered, bot_token, now=now_ts) is None

    invite_client = Client()
    invite_client.get("/login?invite=inv_webinvite2026")
    tg_resp = invite_client.get("/auth/telegram", data=widget_data)
    assert tg_resp.status_code == 303
    assert tg_resp.headers["location"] == "/"
    ani_reader = Reader.objects.filter(channels__channel="telegram", channels__external_id="888999").first()
    assert ani_reader is not None and ani_reader.is_allowed is True


def test_only_operator_telegram_id_can_access_django_admin_via_magic_login(data_dir, monkeypatch):
    from django.contrib.auth import get_user_model

    init_db()
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "invite")
    monkeypatch.setenv("ONECHAPTER_OPERATOR_TELEGRAM_IDS", "59911481")
    monkeypatch.setenv("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "59911481,777888")
    monkeypatch.setenv("ONECHAPTER_INVITE_CODE", "friendcode2026")

    # Seed initial operator superuser linked to Reader #1 (before any Telegram user connects)
    User = get_user_model()
    op_user = User.objects.create_user(username="operator", password="strong-random-password")
    op_user.is_staff = True
    op_user.is_superuser = True
    op_user.save()
    local_op = get_or_create_local_reader()
    local_op.user = op_user
    local_op.is_operator = True
    local_op.save()

    # 1. Invited friend connects FIRST via invite code — must NOT hijack Reader #1 or become operator
    invited_friend = get_or_create_telegram_reader(
        None,
        123456,
        "Invited Friend",
        invite_code="inv_friendcode2026",
    )
    assert invited_friend.id != local_op.id
    assert invited_friend.is_allowed is True
    assert invited_friend.is_operator is False
    assert invited_friend.user.is_staff is False
    assert invited_friend.user.is_superuser is False

    # 2. Allowlisted non-operator connects — allowed to read, but NOT an operator/admin
    allowlisted_friend = get_or_create_telegram_reader(None, 777888, "Allowlisted Friend")
    assert allowlisted_friend.id != local_op.id
    assert allowlisted_friend.is_allowed is True
    assert allowlisted_friend.is_operator is False

    # 3. Operator (59911481) connects and unifies into Reader #1 (operator superuser)
    operator_reader = get_or_create_telegram_reader(None, 59911481, "Karen")
    assert operator_reader.id == local_op.id
    assert operator_reader.is_operator is True
    assert operator_reader.user.is_superuser is True

    # 4. Invited friend signs in via /login magic link -> reader works (200), /admin/ is 403 Forbidden
    friend_magic = create_magic_login_token(invited_friend.id, 123456)
    friend_client = Client()
    assert friend_client.get(f"/auth/magic?token={friend_magic}").status_code == 303
    assert friend_client.get("/").status_code == 200
    assert friend_client.get("/admin/").status_code == 403
    assert friend_client.get("/admin/login/").status_code == 403

    # 5. Operator (59911481) signs in via /login magic link -> /admin/ opens directly (200)
    op_magic = create_magic_login_token(operator_reader.id, 59911481)
    op_client = Client()
    assert op_client.get(f"/auth/magic?token={op_magic}").status_code == 303
    admin_resp = op_client.get("/admin/")
    assert admin_resp.status_code == 200
    assert "Site administration" in admin_resp.content.decode()


def test_public_mode_operator_isolation_fails_closed_when_operator_ids_empty(data_dir, monkeypatch):
    from django.contrib.auth import get_user_model
    from django.test import override_settings

    init_db()
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    monkeypatch.setenv("ONECHAPTER_OPERATOR_TELEGRAM_IDS", "")
    # Even if Stage-1 friend IDs remain in ONECHAPTER_ALLOWED_TELEGRAM_IDS, public mode must not grant operator
    monkeypatch.setenv("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "444555")

    # Case A: Empty database -> very first public reader must NOT become operator
    first_public = get_or_create_telegram_reader(None, 444555, "First Visitor")
    assert first_public.is_allowed is True
    assert first_public.is_operator is False

    # Case B: Seeded Local reader / privileged superuser exists -> public visitors must NEVER unify into it
    User = get_user_model()
    seeded_su = User.objects.create_user(username="seeded-op", password="strong-password")
    seeded_su.is_staff = True
    seeded_su.is_superuser = True
    seeded_su.save()
    seeded_local_op = Reader.objects.create(
        user=seeded_su,
        display_name="Local reader",
        is_allowed=True,
        is_operator=True,
    )

    second_public = get_or_create_telegram_reader(None, 777111, "Public Stranger")
    assert second_public.id != seeded_local_op.id
    assert second_public.is_allowed is True
    assert second_public.is_operator is False
    assert second_public.user.is_staff is False
    assert second_public.user.is_superuser is False

    # Public stranger gets 403 on /admin/ and cannot see or mutate bot token on /settings
    with override_settings(DEBUG=False):
        stranger_magic = create_magic_login_token(second_public.id, 777111)
        stranger_client = Client()
        assert stranger_client.get(f"/auth/magic?token={stranger_magic}").status_code == 303
        assert stranger_client.get("/admin/").status_code == 403
        settings_html = stranger_client.get("/settings").content.decode()
        assert 'name="telegram_bot_token"' not in settings_html

        # Even the seeded superuser session is denied /admin/ in public mode while ONECHAPTER_OPERATOR_TELEGRAM_IDS is empty
        su_client = Client()
        su_client.force_login(seeded_su)
        assert su_client.get("/admin/").status_code == 403


def test_public_mode_operator_isolation_with_populated_operator_ids(data_dir, monkeypatch):
    from django.contrib.auth import get_user_model
    from django.test import override_settings

    init_db()
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    monkeypatch.setenv("ONECHAPTER_OPERATOR_TELEGRAM_IDS", "59911481")

    User = get_user_model()
    op_user = User.objects.create_user(username="prod-operator", password="strong-password")
    op_user.is_staff = True
    op_user.is_superuser = True
    op_user.save()
    local_op = get_or_create_local_reader()
    local_op.user = op_user
    local_op.is_operator = True
    local_op.save()

    # Public stranger connects BEFORE the operator -> isolated non-operator Reader
    early_visitor = get_or_create_telegram_reader(None, 101010, "Early Public Reader")
    assert early_visitor.id != local_op.id
    assert early_visitor.is_allowed is True
    assert early_visitor.is_operator is False

    # Configured operator connects AFTER the public stranger -> unifies into local_op
    op_reader = get_or_create_telegram_reader(None, 59911481, "Karen")
    assert op_reader.id == local_op.id
    assert op_reader.is_operator is True

    with override_settings(DEBUG=False):
        visitor_client = Client()
        assert visitor_client.get(
            f"/auth/magic?token={create_magic_login_token(early_visitor.id, 101010)}"
        ).status_code == 303
        assert visitor_client.get("/").status_code == 200
        assert visitor_client.get("/admin/").status_code == 403

        op_client = Client()
        assert op_client.get(
            f"/auth/magic?token={create_magic_login_token(op_reader.id, 59911481)}"
        ).status_code == 303
        assert op_client.get("/admin/").status_code == 200


def test_magic_auth_access_mode_upgrade_and_operator_blocking_in_public_mode(data_dir, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from onechapter.bot.handlers import BLOCKED_ACCOUNT_MESSAGE, cmd_start

    init_db()
    monkeypatch.setenv("DJANGO_DEBUG", "0")
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "invite")
    monkeypatch.setenv("ONECHAPTER_INVITE_CODE", "launch2026")

    # 1. Waitlisted visitor hits bot in invite mode -> is_allowed=False, magic link denied
    waitlisted = get_or_create_telegram_reader(None, 303030, "Waitlisted Reader")
    assert waitlisted.is_allowed is False
    magic_token = create_magic_login_token(waitlisted.id, 303030)

    client = Client()
    denied = client.get(f"/auth/magic?token={magic_token}")
    assert denied.status_code == 303
    assert "notice=invite_only" in denied.headers["location"]

    # 2. Server switches to ONECHAPTER_ACCESS_MODE=public -> magic_auth re-checks access mode and upgrades waitlisted reader!
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    allowed_resp = client.get(f"/auth/magic?token={magic_token}")
    assert allowed_resp.status_code == 303
    assert allowed_resp.headers["location"] == "/"
    waitlisted.refresh_from_db()
    assert waitlisted.is_allowed is True
    assert client.get("/").status_code == 200

    # 3. Start an active reading plan, then operator blocks the abusive account in public mode
    from onechapter.seed import seed_builtin_books

    seed_builtin_books()
    slow = get_book("slow-reading")
    start_book(None, waitlisted.id, slow.id)
    assert any(p.reader_id == waitlisted.id for p in list_active_plans())

    set_reader_blocked(waitlisted.id, blocked=True)
    waitlisted.refresh_from_db()
    assert waitlisted.is_blocked is True
    assert waitlisted.is_allowed is False

    # Active web session immediately receives 403, magic link is rejected, and active plans exclude blocked reader
    assert client.get("/").status_code == 403
    relogin = Client().get(f"/auth/magic?token={magic_token}")
    assert relogin.status_code == 303
    assert "notice=blocked" in relogin.headers["location"]
    assert not any(p.reader_id == waitlisted.id for p in list_active_plans())

    # Sending a new Telegram message in public mode does NOT auto-re-enable the blocked reader!
    replies: list[str] = []

    async def fake_reply(text: str, **_kwargs):
        replies.append(text)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=303030, full_name="Waitlisted Reader", username="waitlisted"),
        message=SimpleNamespace(reply_text=fake_reply),
    )
    asyncio.run(cmd_start(update, SimpleNamespace(args=[])))
    assert replies == [BLOCKED_ACCOUNT_MESSAGE]
    waitlisted.refresh_from_db()
    assert waitlisted.is_blocked is True
    assert waitlisted.is_allowed is False

    # Also verify that if an operator directly sets is_allowed=False on a public-mode reader (even without set_reader_blocked),
    # next message in public mode STILL does not auto-re-enable them
    abusive2 = get_or_create_telegram_reader(None, 404040, "Abusive Two")
    assert abusive2.is_allowed is True
    abusive2.is_allowed = False
    abusive2.save(update_fields=["is_allowed"])

    again = get_or_create_telegram_reader(None, 404040, "Abusive Two")
    assert again.is_allowed is False
