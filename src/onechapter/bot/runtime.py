from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.error import InvalidToken, TelegramError
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from onechapter.bot.handlers import (
    cmd_begin,
    cmd_books,
    cmd_help,
    cmd_login,
    cmd_pause,
    cmd_progress,
    cmd_read,
    cmd_resume,
    cmd_start,
    cmd_time,
    cmd_today,
    cmd_tz,
    cmd_yesterday,
    on_document,
    send_due_chapters,
)

log = logging.getLogger(__name__)


def build_application(token: str) -> Application:
    app = (
        Application.builder()
        .token(token)
        .connect_timeout(15.0)
        .read_timeout(15.0)
        .write_timeout(15.0)
        .get_updates_connect_timeout(15.0)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("yesterday", cmd_yesterday))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("time", cmd_time))
    app.add_handler(CommandHandler("tz", cmd_tz))
    app.add_handler(CommandHandler("books", cmd_books))
    app.add_handler(CommandHandler("read", cmd_read))
    app.add_handler(CommandHandler("begin", cmd_begin))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    return app


def build_scheduler(application: Application) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    async def tick() -> None:
        try:
            await send_due_chapters(application.bot)
        except Exception:
            log.exception("Daily delivery tick failed")

    scheduler.add_job(tick, "interval", minutes=1, id="daily-chapter", replace_existing=True)
    return scheduler


class TelegramRuntime:
    """Start and stop polling without restarting the web process."""

    def __init__(self) -> None:
        self.application: Application | None = None
        self.scheduler: AsyncIOScheduler | None = None
        self.username: str | None = None
        self.bot_id: int | None = None

    @property
    def running(self) -> bool:
        return self.application is not None

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "username": self.username,
            "bot_id": self.bot_id,
        }

    async def start(self, token: str) -> str:
        token = (token or "").strip()
        if not token:
            raise ValueError("Paste a token from @BotFather first.")
        await self.stop()
        application = build_application(token)
        await application.initialize()
        try:
            me = await application.bot.get_me()
        except (InvalidToken, TelegramError) as exc:
            await application.shutdown()
            raise ValueError("Telegram rejected that token. Copy it again from @BotFather.") from exc
        await application.start()
        assert application.updater is not None
        await application.updater.start_polling(drop_pending_updates=True)
        scheduler = build_scheduler(application)
        scheduler.start()
        self.application = application
        self.scheduler = scheduler
        self.username = me.username
        self.bot_id = me.id
        log.info("Telegram bot @%s polling", me.username)
        return me.username or str(me.id)

    async def stop(self) -> None:
        scheduler = self.scheduler
        application = self.application
        self.scheduler = None
        self.application = None
        self.username = None
        self.bot_id = None
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                log.exception("Error stopping delivery scheduler")
        if application is None:
            return
        try:
            if application.updater is not None and application.updater.running:
                await application.updater.stop()
            if application.running:
                await application.stop()
            await application.shutdown()
        except Exception:
            log.exception("Error stopping Telegram bot")


runtime = TelegramRuntime()
