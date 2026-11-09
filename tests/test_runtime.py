from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from onechapter.bot.handlers import (
    INVITE_ONLY_MESSAGE,
    cmd_books,
    cmd_login,
    cmd_read,
    cmd_start,
    send_due_chapters,
)
from onechapter.bot.runtime import TelegramRuntime
from onechapter.library import bootstrap


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str, **kwargs) -> None:
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

        # 4. Daily delivery tick sends due chapter to invited user (9999) when they start a plan
        read2_update, _ = _make_update(9999, "Outsider", "outsider")
        await cmd_read(read2_update, SimpleNamespace(args=["slow-reading"]))
        # Already delivered today via cmd_read, so send_due_chapters at 09:00 same day sends 0
        bot = FakeBot()
        now_today = datetime.now(ZoneInfo("Asia/Yerevan")).replace(hour=9, minute=0)
        sent_count = await send_due_chapters(bot, now=now_today)
        assert sent_count == 0

    asyncio.run(scenario())
