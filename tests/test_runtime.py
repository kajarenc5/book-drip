from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from onechapter.bot.handlers import (
    INVITE_ONLY_MESSAGE,
    _collect_due_deliveries,
    cmd_begin,
    cmd_books,
    cmd_login,
    cmd_pause,
    cmd_read,
    cmd_resume,
    cmd_start,
    cmd_stop,
    cmd_time,
    on_document,
    on_text_reflection,
    send_due_chapters,
)
from onechapter.bot.runtime import TelegramRuntime, build_application
from onechapter.config import bot_polling_disabled
from onechapter.db import init_db
from onechapter.ingest.split import ChapterDraft, SplitResult
from onechapter.library import (
    bootstrap,
    get_book,
    get_or_create_telegram_reader,
    start_book,
    upsert_book_from_split,
)
from onechapter.reader.models import ChannelIdentity, Delivery, Reflection


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class FakeBot:
    def __init__(self, fail_for_chat_ids: set[int] | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_for = fail_for_chat_ids or set()

    async def send_message(self, *, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self.fail_for:
            raise RuntimeError(f"Simulated Telegram Forbidden for {chat_id}")
        self.sent.append((chat_id, text))


def _make_update(user_id: int, full_name: str = "Reader", username: str = "reader"):
    msg = FakeMessage()
    user = SimpleNamespace(id=user_id, full_name=full_name, username=username)
    return SimpleNamespace(effective_user=user, message=msg), msg


def test_runtime_starts_idle():
    rt = TelegramRuntime()
    assert rt.running is False
    snap = rt.snapshot()
    assert snap == {"running": False, "username": None, "bot_id": None}


def test_bot_handlers_invite_mode_and_due_delivery(data_dir, monkeypatch):
    bootstrap()
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "invite")
    monkeypatch.setenv("ONECHAPTER_ALLOWED_TELEGRAM_IDS", "4242")
    monkeypatch.setenv("ONECHAPTER_INVITE_CODE", "secret-invite")

    async def scenario():
        # 1. Uninvited user is blocked on /start and /books
        uninvited_update, uninvited_msg = _make_update(9999, "Outsider", "outsider")
        await cmd_start(uninvited_update, SimpleNamespace(args=[]))
        assert uninvited_msg.replies == [INVITE_ONLY_MESSAGE]

        uninvited_books_update, uninvited_books_msg = _make_update(9999, "Outsider", "outsider")
        await cmd_books(uninvited_books_update, SimpleNamespace(args=[]))
        assert uninvited_books_msg.replies == [INVITE_ONLY_MESSAGE]

        # 2. Uninvited userredeems invite code (/start inv_secret-invite) -> allowed!
        invited_update, invited_msg = _make_update(9999, "Outsider", "outsider")
        await cmd_start(invited_update, SimpleNamespace(args=["inv_secret-invite"]))
        assert "slow-reading companion" in invited_msg.replies[0]

        # 3. Allowlisted user starts slow-reading and requests a /login magic link
        op_update, op_msg = _make_update(4242, "Karen", "karen")
        await cmd_read(op_update, SimpleNamespace(args=["slow-reading"]))
        assert any("One page a day" in r for r in op_msg.replies)

        login_update, login_msg = _make_update(4242, "Karen", "karen")
        await cmd_login(login_update, SimpleNamespace(args=[]))
        assert "/auth/magic?token=" in login_msg.replies[0]

        # 4. Validate /time command rejects malformed input and normalizes valid input
        bad_time_update, bad_time_msg = _make_update(4242, "Karen", "karen")
        await cmd_time(bad_time_update, SimpleNamespace(args=["25:99"]))
        assert "Example: /time 08:00" in bad_time_msg.replies[0]

        good_time_update, good_time_msg = _make_update(4242, "Karen", "karen")
        await cmd_time(good_time_update, SimpleNamespace(args=["8:05"]))
        assert "08:05" in good_time_msg.replies[0]

        # 5. Daily delivery tick sends due chapter to invited user (9999) when they start a plan
        read2_update, _ = _make_update(9999, "Outsider", "outsider")
        await cmd_read(read2_update, SimpleNamespace(args=["slow-reading"]))
        # Already delivered today via cmd_read, so send_due_chapters at 09:00 same day sends 0
        bot = FakeBot()
        now_today = datetime.now(ZoneInfo("Asia/Yerevan")).replace(hour=9, minute=0)
        sent_count = await send_due_chapters(bot, now=now_today)
        assert sent_count == 0

        # Next day at 09:00: if chat 4242 fails to send, chat 9999 still receives Day 2!
        now_tomorrow = now_today + timedelta(days=1)
        bot_isolated = FakeBot(fail_for_chat_ids={4242})
        sent_tomorrow = await send_due_chapters(bot_isolated, now=now_tomorrow)
        assert sent_tomorrow == 1
        assert any(chat_id == 9999 for chat_id, _ in bot_isolated.sent)

        # 6. Sending a text message in Telegram saves a reflection on today's chapter
        note_update, note_msg = _make_update(4242, "Karen", "karen")
        note_update.message.text = "A quiet morning reflection from Telegram."
        await on_text_reflection(note_update, SimpleNamespace())
        assert any("Saved note on day 1" in r for r in note_msg.replies)

        # 7. Oversized Telegram document upload is rejected before download
        monkeypatch.setenv("ONECHAPTER_MAX_UPLOAD_BYTES", "100")
        doc_update, doc_msg = _make_update(4242, "Karen", "karen")
        doc_update.message.document = SimpleNamespace(file_name="big.mobi", file_size=500, file_id="f1")
        await on_document(doc_update, SimpleNamespace())
        assert any("too large" in r for r in doc_msg.replies)

    asyncio.run(scenario())
    saved_ref = Reflection.objects.filter(source_channel="telegram").first()
    assert saved_ref is not None
    assert saved_ref.body == "A quiet morning reflection from Telegram."


def test_bot_polling_disabled_flag(monkeypatch):
    monkeypatch.delenv("ONECHAPTER_DISABLE_BOT_POLLING", raising=False)
    assert bot_polling_disabled() is False
    monkeypatch.setenv("ONECHAPTER_DISABLE_BOT_POLLING", "1")
    assert bot_polling_disabled() is True


def test_forbidden_and_chat_not_found_disable_delivery_without_infinite_retries(data_dir, monkeypatch):
    from telegram.error import BadRequest, Forbidden

    bootstrap()
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    slow = get_book("slow-reading")
    now = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("Asia/Yerevan"))

    r_blocked = get_or_create_telegram_reader(None, 5001, "Blocked User")
    r_missing = get_or_create_telegram_reader(None, 5002, "Deleted Chat User")
    r_entity_err = get_or_create_telegram_reader(None, 5003, "Transient Parse Error User")
    r_healthy = get_or_create_telegram_reader(None, 5004, "Healthy User")

    for r in (r_blocked, r_missing, r_entity_err, r_healthy):
        start_book(None, r.id, slow.id, now=now)

    attempts: dict[int, int] = {}

    class ErrorBot:
        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []

        async def send_message(self, *, chat_id: int, text: str, **kwargs) -> None:
            attempts[chat_id] = attempts.get(chat_id, 0) + 1
            if chat_id == 5001:
                raise Forbidden("Forbidden: bot was blocked by the user")
            if chat_id == 5002:
                raise BadRequest("Chat not found")
            if chat_id == 5003:
                raise BadRequest("Can't parse entities: unsupported start tag")
            self.sent.append((chat_id, text))

    bot = ErrorBot()
    sent_first = asyncio.run(send_due_chapters(bot, now=now))
    assert sent_first == 1
    assert [cid for cid, _ in bot.sent] == [5004]
    assert attempts == {5001: 1, 5002: 1, 5003: 1, 5004: 1}

    ci_blocked = ChannelIdentity.objects.get(reader=r_blocked, channel="telegram")
    ci_missing = ChannelIdentity.objects.get(reader=r_missing, channel="telegram")
    ci_entity = ChannelIdentity.objects.get(reader=r_entity_err, channel="telegram")
    assert ci_blocked.is_delivery_enabled is False
    assert ci_blocked.metadata.get("delivery_disabled_reason") == "telegram_unreachable"
    assert ci_missing.is_delivery_enabled is False
    assert ci_missing.metadata.get("delivery_disabled_reason") == "telegram_unreachable"
    # Non-chat BadRequest must NOT permanently disable the reader's channel
    assert ci_entity.is_delivery_enabled is True

    # Next 60-second tick: blocked (5001) and chat-not-found (5002) are NOT retried!
    sent_second = asyncio.run(send_due_chapters(bot, now=now + timedelta(minutes=1)))
    assert sent_second == 0
    assert attempts[5001] == 1
    assert attempts[5002] == 1
    assert attempts[5004] == 1
    assert attempts[5003] == 2


def test_retry_after_defers_batch_and_resumes_without_resending_delivered_chunks(data_dir, monkeypatch):
    from telegram.error import RetryAfter

    bootstrap()
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    now = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("Asia/Yerevan"))

    # Create a book with a multi-chunk chapter (> 3900 chars -> 2 chunks)
    long_para_a = "First half paragraph about deliberate reading. " * 55
    long_para_b = "Second half paragraph about quiet reflection. " * 55
    long_book = upsert_book_from_split(
        None,
        SplitResult(
            title="Long Chapter Book",
            author="Author",
            profile="generic",
            chapters=[
                ChapterDraft(
                    index=0,
                    title="Extended Essay",
                    body=f"{long_para_a}\n\n{long_para_b}",
                ),
            ],
        ),
        slug="long-chapter-book",
    )

    r1 = get_or_create_telegram_reader(None, 6101, "MultiChunk Reader")
    r2 = get_or_create_telegram_reader(None, 6102, "Second Queued Reader")
    start_book(None, r1.id, long_book.id, now=now)
    start_book(None, r2.id, long_book.id, now=now)

    class RateLimitedBot:
        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []
            self.raise_429_once = True

        async def send_message(self, *, chat_id: int, text: str, **kwargs) -> None:
            # Let chunk 1 of r1 succeed, then trigger HTTP 429 RetryAfter(30s) on chunk 2!
            if chat_id == 6101 and len(self.sent) == 1 and self.raise_429_once:
                self.raise_429_once = False
                raise RetryAfter(timedelta(seconds=30))
            self.sent.append((chat_id, text))

    bot = RateLimitedBot()

    # Tick 1 at 09:00:00 -> chunk 1 of r1 sent, chunk 2 hits RetryAfter(30s), batch breaks cleanly (r2 deferred)
    sent_t1 = asyncio.run(send_due_chapters(bot, now=now))
    assert sent_t1 == 0
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 6101
    assert "First half paragraph" in bot.sent[0][1]
    assert Delivery.objects.count() == 0

    # Tick inside RetryAfter window (+10s < 30s) -> backs off immediately without calling send_message
    sent_backoff = asyncio.run(send_due_chapters(bot, now=now + timedelta(seconds=10)))
    assert sent_backoff == 0
    assert len(bot.sent) == 1

    # Tick 2 at 09:01:00 (+60s >= 30s) -> sends ONLY chunk 2 to r1 (does NOT resend chunk 1!) and delivers r2!
    sent_t2 = asyncio.run(send_due_chapters(bot, now=now + timedelta(minutes=1)))
    assert sent_t2 == 2
    r1_messages = [txt for cid, txt in bot.sent if cid == 6101]
    r2_messages = [txt for cid, txt in bot.sent if cid == 6102]
    assert len(r1_messages) == 2
    assert "First half paragraph" in r1_messages[0]
    assert "Second half paragraph" in r1_messages[1]
    assert len(r2_messages) == 2
    assert Delivery.objects.count() == 2


def test_stop_unsubscribe_and_start_resume_toggle_telegram_delivery(data_dir, monkeypatch):
    bootstrap()
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")
    now = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("Asia/Yerevan"))

    async def scenario():
        # 1. Start reading via /begin -> with Tarasov also in DB, /begin must still pick slow-reading first!
        await asyncio.to_thread(
            upsert_book_from_split,
            None,
            SplitResult(
                title="Технология жизни",
                author="Владимир Тарасов",
                profile="tarasov",
                chapters=[ChapterDraft(index=0, title="Притча", body="Короткий текст главы для проверки.")],
            ),
            slug="tehnologiya-zhizni",
            language="ru",
        )
        upd_begin, msg_begin = _make_update(7101, "Narek", "narek")
        await cmd_begin(upd_begin, SimpleNamespace(args=[]))
        assert any("One page a day" in r for r in msg_begin.replies)

        # 2. /stop disables daily Telegram delivery
        upd_stop, msg_stop = _make_update(7101, "Narek", "narek")
        await cmd_stop(upd_stop, SimpleNamespace(args=[]))
        assert any("Daily Telegram delivery is off" in r for r in msg_stop.replies)

        ci = await asyncio.to_thread(
            lambda: ChannelIdentity.objects.get(external_id="7101", channel="telegram")
        )
        assert ci.is_delivery_enabled is False

        # Tomorrow at 09:00: send_due_chapters skips stopped user
        bot = FakeBot()
        tomorrow = now + timedelta(days=2)
        assert await send_due_chapters(bot, now=tomorrow) == 0

        # 3. /resume re-enables Telegram delivery even when plan wasn't paused
        upd_resume, msg_resume = _make_update(7101, "Narek", "narek")
        await cmd_resume(upd_resume, SimpleNamespace(args=[]))
        assert any("back on" in r or "Resumed" in r for r in msg_resume.replies)
        assert await send_due_chapters(bot, now=tomorrow) == 1

        # 4. /unsubscribe (mapped to cmd_stop) disables again, and /start connect re-enables cleanly!
        app = build_application("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
        command_names = {
            cmd
            for group_handlers in app.handlers.values()
            for h in group_handlers
            for cmd in getattr(h, "commands", ())
        }
        assert {"stop", "unsubscribe", "start", "resume"} <= command_names

        upd_unsub, _ = _make_update(7101, "Narek", "narek")
        await cmd_stop(upd_unsub, SimpleNamespace(args=[]))
        ci_off = await asyncio.to_thread(
            lambda: ChannelIdentity.objects.get(external_id="7101", channel="telegram")
        )
        assert ci_off.is_delivery_enabled is False

        upd_start, msg_start = _make_update(7101, "Narek", "narek")
        await cmd_start(upd_start, SimpleNamespace(args=["connect"]))
        assert any("slow-reading companion" in r for r in msg_start.replies)
        ci_on = await asyncio.to_thread(
            lambda: ChannelIdentity.objects.get(external_id="7101", channel="telegram")
        )
        assert ci_on.is_delivery_enabled is True

        # 5. Pause + stop -> /resume resumes both plan and Telegram delivery
        upd_pause, _ = _make_update(7101, "Narek", "narek")
        await cmd_pause(upd_pause, SimpleNamespace(args=[]))
        await cmd_stop(upd_unsub, SimpleNamespace(args=[]))
        upd_resume2, msg_resume2 = _make_update(7101, "Narek", "narek")
        await cmd_resume(upd_resume2, SimpleNamespace(args=[]))
        assert any("Today's chapter is the one you left on" in r for r in msg_resume2.replies)

        # 6. Reflection length limit in Telegram (> 10,000 chars rejected)
        upd_huge, msg_huge = _make_update(7101, "Narek", "narek")
        upd_huge.message.text = "x" * 10_001
        await on_text_reflection(upd_huge, SimpleNamespace())
        assert any("too long" in r for r in msg_huge.replies)
        assert await asyncio.to_thread(Reflection.objects.count) == 0

    asyncio.run(scenario())


def test_runtime_start_keeps_pending_updates_and_due_collection_avoids_n_plus_one(data_dir, monkeypatch):
    from django.db import connections
    from django.test.utils import CaptureQueriesContext

    bootstrap()
    monkeypatch.setenv("ONECHAPTER_ACCESS_MODE", "public")

    # 1. Verify init_db() executes 0 SQL queries once tables have been verified
    init_db()
    with CaptureQueriesContext(connections["default"]) as init_queries:
        for _ in range(10):
            init_db()
    assert len(init_queries) == 0

    # 2. Seed 10 active readers across 2 books and verify _collect_due_deliveries uses O(1) queries (<= 5)
    slow = get_book("slow-reading")
    prince = get_book("the-prince")
    now = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("Asia/Yerevan"))
    for idx in range(10):
        reader = get_or_create_telegram_reader(None, 9000 + idx, f"Reader {idx}")
        chosen_book = slow if idx % 2 == 0 else prince
        start_book(None, reader.id, chosen_book.id, now=now)

    with CaptureQueriesContext(connections["default"]) as due_queries:
        due = asyncio.run(_collect_due_deliveries(now))
    assert len(due) == 10
    assert len(due_queries) <= 5

    # 3. Verify TelegramRuntime.start passes drop_pending_updates=False to start_polling
    polling_kwargs: list[dict] = []

    class FakeUpdater:
        running = True

        async def start_polling(self, **kwargs):
            polling_kwargs.append(kwargs)

        async def stop(self):
            self.running = False

    class FakeApp:
        running = True

        def __init__(self):
            self.updater = FakeUpdater()
            self.bot = SimpleNamespace(
                get_me=self._get_me,
            )

        async def _get_me(self):
            return SimpleNamespace(username="one_chapter_bot", id=12345)

        async def initialize(self):
            pass

        async def start(self):
            pass

        async def stop(self):
            self.running = False

        async def shutdown(self):
            pass

    monkeypatch.setattr("onechapter.bot.runtime.build_application", lambda _tok: FakeApp())

    async def check_start_stop():
        rt = TelegramRuntime()
        uname = await rt.start("123456:ABC-DEF")
        assert uname == "one_chapter_bot"
        assert polling_kwargs == [{"drop_pending_updates": False}]
        await rt.stop()

    asyncio.run(check_start_stop())
