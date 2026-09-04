from __future__ import annotations

from datetime import datetime

from onechapter.db import db
from onechapter.library import (
    active_plan_for_reader,
    already_delivered,
    chapter_count,
    get_book,
    get_chapter,
    get_or_create_telegram_reader,
    ingest_file,
    list_books,
    record_delivery,
    save_upload,
    start_book,
    state_for_plan,
    pause_plan,
    resume_plan,
    update_reader,
)
from onechapter.reading import due_for_delivery
from onechapter.telegram_format import format_chapter_messages


HELP = """One chapter a day. No more.

/today — reread today's chapter
/yesterday — the previous one
/progress — where you are
/pause and /resume — freeze the calendar
/time 08:00 — morning delivery
/tz Europe/Moscow — your timezone
/books — library
/begin — start (or restart) the current book

Send a .mobi, .epub, or .fb2 file to add a book. For Tarasov's Технология жизни, the bot splits the short titled essays, not the four large parts.
"""


def _name(update) -> str | None:
    user = update.effective_user
    if not user:
        return None
    return user.full_name


async def cmd_start(update, context) -> None:
    with db() as conn:
        get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
    await update.message.reply_text(
        "This is a slow-reading companion.\n\n"
        "It will send you one short, dense chapter each day, then wait.\n\n" + HELP
    )


async def cmd_help(update, context) -> None:
    await update.message.reply_text(HELP)


async def cmd_books(update, context) -> None:
    with db() as conn:
        books = list_books(conn)
    if not books:
        await update.message.reply_text("The library is empty. Send a .mobi file to begin.")
        return
    lines = []
    for book in books:
        lines.append(f"• {book['title']} — {book['chapter_count']} days (/read {book['slug']})")
    await update.message.reply_text("Library:\n" + "\n".join(lines))


async def cmd_read(update, context) -> None:
    slug = " ".join(context.args).strip() if context.args else ""
    if not slug:
        await update.message.reply_text("Use /read <book-slug>. See /books.")
        return
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        book = get_book(conn, slug)
        if not book:
            await update.message.reply_text("No book with that name. See /books.")
            return
        state = start_book(conn, reader["id"], book["id"])
        await _send_today(update, conn, state, book, force=True)


async def cmd_begin(update, context) -> None:
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        plan = active_plan_for_reader(conn, reader["id"])
        books = list_books(conn)
        if plan:
            book = get_book(conn, plan["slug"])
            state = start_book(conn, reader["id"], plan["book_id"])
            await _send_today(update, conn, state, book, force=True)
            return
        tarasov = next((b for b in books if b["profile"] == "tarasov"), None)
        chosen = tarasov or next((b for b in books if b["slug"] == "slow-reading"), books[0] if books else None)
        if not chosen:
            await update.message.reply_text("Send your .mobi of Технология жизни first.")
            return
        state = start_book(conn, reader["id"], chosen["id"])
        await _send_today(update, conn, state, chosen, force=True)


async def cmd_today(update, context) -> None:
    await _reply_position(update, offset=0)


async def cmd_yesterday(update, context) -> None:
    await _reply_position(update, offset=-1)


async def cmd_progress(update, context) -> None:
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        plan = active_plan_for_reader(conn, reader["id"])
        if not plan:
            await update.message.reply_text("No active book. Use /books then /read <slug>.")
            return
        state = state_for_plan(conn, plan)
        status = "paused" if state.status == "paused" else "reading"
        await update.message.reply_text(
            f"{plan['book_title']}\n"
            f"{status}: day {state.unlocked_through + 1} of {state.total}\n"
            f"Delivery at {state.delivery_hhmm} ({state.timezone})"
        )


async def cmd_pause(update, context) -> None:
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        plan = active_plan_for_reader(conn, reader["id"])
        if not plan:
            await update.message.reply_text("Nothing to pause.")
            return
        pause_plan(conn, plan["id"])
    await update.message.reply_text("Paused. The calendar will not advance until /resume.")


async def cmd_resume(update, context) -> None:
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        plan = active_plan_for_reader(conn, reader["id"])
        if not plan:
            await update.message.reply_text("Nothing to resume.")
            return
        resume_plan(conn, plan["id"])
    await update.message.reply_text("Resumed. Today's chapter is the one you left on.")


async def cmd_time(update, context) -> None:
    value = " ".join(context.args).strip() if context.args else ""
    if not value:
        await update.message.reply_text("Example: /time 08:00")
        return
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        update_reader(conn, reader["id"], delivery_hhmm=value)
    await update.message.reply_text(f"Daily delivery set to {value}.")


async def cmd_tz(update, context) -> None:
    value = " ".join(context.args).strip() if context.args else ""
    if not value:
        await update.message.reply_text("Example: /tz Europe/Moscow  or  /tz Asia/Yerevan")
        return
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
    except Exception:
        await update.message.reply_text("Unknown timezone. Use an IANA name like Europe/Moscow.")
        return
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        update_reader(conn, reader["id"], timezone=value)
    await update.message.reply_text(f"Timezone set to {value}.")


async def on_document(update, context) -> None:
    doc = update.message.document
    name = doc.file_name or "book"
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    from onechapter.ingest.extract import SUPPORTED_SUFFIXES

    if suffix not in SUPPORTED_SUFFIXES:
        await update.message.reply_text("Send a .mobi, .epub, .fb2, .txt, or .html file.")
        return
    await update.message.reply_text("Reading the file and cutting it into daily chapters…")
    tg_file = await context.bot.get_file(doc.file_id)
    data = await tg_file.download_as_bytearray()
    path = save_upload(name, bytes(data))
    try:
        with db() as conn:
            book = ingest_file(path, conn)
            reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
            start_book(conn, reader["id"], book["id"])
        first = _first_title(book)
        await update.message.reply_text(
            f"Ready: {book['title']}\n"
            f"{book['chapter_count']} short chapters — one per day.\n"
            f"First: {first}\n\n"
            "Send /today to read the first one now."
        )
    except Exception as exc:
        await update.message.reply_text(f"Could not split that file: {exc}")


def _first_title(book) -> str:
    with db() as conn:
        chapter = get_chapter(conn, book["id"], 0)
        return chapter["title"] if chapter else "—"


async def _reply_position(update, offset: int) -> None:
    with db() as conn:
        reader = get_or_create_telegram_reader(conn, update.effective_user.id, _name(update))
        plan = active_plan_for_reader(conn, reader["id"])
        if not plan:
            await update.message.reply_text("Start with /begin, or send the book file.")
            return
        state = state_for_plan(conn, plan)
        if state.unlocked_through < 0:
            await update.message.reply_text("The plan has not started yet. /begin")
            return
        position = state.unlocked_through + offset
        if position < 0:
            await update.message.reply_text("That is before the beginning of the book.")
            return
        if position > state.unlocked_through:
            await update.message.reply_text("Tomorrow's chapter is still locked.")
            return
        book = {"id": plan["book_id"], "title": plan["book_title"], "slug": plan["slug"]}
        await _send_chapter(update, conn, state, book, position, channel="telegram-request")


async def _send_today(update, conn, state, book, force: bool = False) -> None:
    position = state.today_position
    if position is None:
        await update.message.reply_text("Could not start the book.")
        return
    await _send_chapter(update, conn, state, book, position, channel="telegram", force=force)


async def _send_chapter(update, conn, state, book, position: int, channel: str, force: bool = False) -> None:
    book_id = book["id"]
    keys = book.keys()
    book_title = book["book_title"] if "book_title" in keys else book["title"]
    chapter = get_chapter(conn, book_id, position)
    if chapter is None:
        await update.message.reply_text("Chapter missing.")
        return
    nxt = get_chapter(conn, book_id, position + 1)
    messages = format_chapter_messages(
        day=position + 1,
        total=state.total or chapter_count(conn, book_id),
        title=chapter["title"],
        part=chapter["part"],
        body=chapter["body"],
        next_title=nxt["title"] if nxt else None,
        book_title=book_title,
    )
    for text in messages:
        await update.message.reply_text(text, parse_mode="HTML")
    if channel.startswith("telegram"):
        record_delivery(conn, state.plan_id, state.today.isoformat(), position, channel)


async def send_due_chapters(bot, now: datetime | None = None) -> int:
    from onechapter.library import list_active_plans

    sent = 0
    with db() as conn:
        plans = list_active_plans(conn)
        due = []
        for plan in plans:
            state = state_for_plan(conn, plan, now)
            if not due_for_delivery(state, now):
                continue
            if already_delivered(conn, state.plan_id, state.today.isoformat(), "telegram"):
                continue
            position = state.today_position
            if position is None:
                continue
            chapter = get_chapter(conn, plan["book_id"], position)
            nxt = get_chapter(conn, plan["book_id"], position + 1)
            messages = format_chapter_messages(
                day=position + 1,
                total=state.total,
                title=chapter["title"],
                part=chapter["part"],
                body=chapter["body"],
                next_title=nxt["title"] if nxt else None,
                book_title=plan["book_title"],
            )
            due.append((plan["telegram_id"], state, messages, position))
        for telegram_id, state, messages, position in due:
            for text in messages:
                await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
            record_delivery(conn, state.plan_id, state.today.isoformat(), position, "telegram")
            sent += 1
    return sent
