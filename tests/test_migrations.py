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
    reflections_for,
    reprocess_book,
    start_book,
    upsert_book_from_split,
)
from onechapter.reader.models import Book, ChannelIdentity, Chapter, Reader, Reflection


def test_django_migrations_create_all_domain_tables(data_dir):
    init_db()
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
    # Create initial operator reader first
    operator = get_or_create_telegram_reader(None, 10001, "Operator")
    assert operator.is_allowed is True

    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "invite")
    monkeypatch.setenv("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "10001,20002")
    monkeypatch.setenv("ONECHAPTER_INVITE_CODE", "slowread2026")

    # Uninvited stranger is created with is_allowed=False
    stranger = get_or_create_telegram_reader(None, 99999, "Stranger")
    assert stranger.is_allowed is False

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


def test_s3_storage_and_operator_reprocess_roundtrip(data_dir, tmp_path, monkeypatch):
    init_db()
    monkeypatch.setenv("ONECHAPTER_S3_BUCKET", "onechapter-test-bucket")

    objects: dict[tuple[str, str], bytes] = {}

    class FakeS3Body:
        def __init__(self, raw: bytes):
            self._raw = raw

        def read(self) -> bytes:
            return self._raw

    class FakeS3Client:
        def put_object(self, *, Bucket, Key, Body, ServerSideEncryption):
            assert ServerSideEncryption == "AES256"
            objects[(Bucket, Key)] = Body

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
    assert ("onechapter-test-bucket", expected_key) in objects

    # Delete local file and verify operator reprocess fetches from S3 and updates in place
    sample.unlink()
    reprocessed = reprocess_book(book.slug, s3_client=fake_s3)
    assert reprocessed.id == book.id
    assert reprocessed.chapter_count == 2


def test_telegram_login_widget_and_magic_link_auth(data_dir, monkeypatch):
    init_db()
    bot_token = "123456789:ABCDEF_test_secret_bot_token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)

    reader = get_or_create_telegram_reader(None, 555123, "Tigran")
    magic = create_magic_login_token(reader.id, 555123)
    verified_magic = verify_magic_login_token(magic)
    assert verified_magic is not None
    assert verified_magic["reader_id"] == reader.id

    client = Client()
    resp = client.get(f"/auth/magic?token={magic}")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    # Test Telegram Login Widget HMAC-SHA256 verification
    now_ts = int(time.time())
    widget_data = {
        "id": "555123",
        "first_name": "Tigran",
        "username": "tigran_r",
        "auth_date": str(now_ts),
    }
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(widget_data.items()))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    widget_data["hash"] = hmac.new(secret_key, check_str.encode("utf-8"), hashlib.sha256).hexdigest()

    assert verify_telegram_login_data(widget_data, bot_token, now=now_ts) is not None
    tampered = dict(widget_data, first_name="Mallory")
    assert verify_telegram_login_data(tampered, bot_token, now=now_ts) is None
