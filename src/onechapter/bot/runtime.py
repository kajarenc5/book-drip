from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from onechapter.bot.handlers import (
    cmd_begin,
    cmd_books,
    cmd_help,
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
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
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
