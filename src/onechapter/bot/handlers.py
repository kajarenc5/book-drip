from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async

from onechapter.auth import create_magic_login_token
from onechapter.config import public_url
from onechapter.library import (
    active_plan_for_reader,
    already_delivered,
    chapter_count,
    get_book,
    get_chapter,
    get_or_create_telegram_reader,
    ingest_file,
    list_active_plans,
    list_books,
    pause_plan,
    record_delivery,
    resume_plan,
    save_upload,
    start_book,
    state_for_plan,
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
/login — open the web reader signed in as you

Send a .mobi, .epub, or .fb2 file to add a book to the library. For Tarasov's Технология жизни, the bot splits the short titled essays, not the four large parts.
"""

INVITE_ONLY_MESSAGE = (
    "One Chapter is currently in invite-only mode.\n\n"
    "Ask the operator for an invite link (/start inv_<code>) to begin reading."
)


def _name(update) -> str | None:
    user = update.effective_user
    if not user:
        return None
    return user.full_name


def _handle(update) -> str | None:
    user = update.effective_user
    if not user:
        return None
    return user.username


@sync_to_async
def _resolve_tg_reader(update, invite_code: str | None = None):
    return get_or_create_telegram_reader(
        None,
        update.effective_user.id,
        _name(update),
        handle=_handle(update),
        invite_code=invite_code,
    )


async def _require_allowed_reader(update, invite_code: str | None = None):
    reader = await _resolve_tg_reader(update, invite_code=invite_code)
    if not reader.is_allowed:
        await update.message.reply_text(INVITE_ONLY_MESSAGE)
        return None
    return reader


async def cmd_start(update, context) -> None:
    arg = context.args[0].strip() if getattr(context, "args", None) else None
    reader = await _require_allowed_reader(update, invite_code=arg)
    if not reader:
        return
    await update.message.reply_text(
        "This is a slow-reading companion.\n\n"
        "It will send you one short, dense chapter each day, then wait.\n\n" + HELP
    )


async def cmd_help(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    await update.message.reply_text(HELP)


async def cmd_login(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    token = create_magic_login_token(reader.id, update.effective_user.id)
    link = f"{public_url()}/auth/magic?token={token}"
    await update.message.reply_text(
        f"Open the web reader signed into your account (valid for 15 minutes):\n{link}"
    )


async def cmd_books(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    books = await sync_to_async(list_books)()
    if not books:
        await update.message.reply_text("The library is empty. Send a .mobi file to begin.")
        return
    lines = []
    for book in books:
        lines.append(f"• {book['title']} — {book['chapter_count']} days (/read {book['slug']})")
    await update.message.reply_text("Library:\n" + "\n".join(lines))


@sync_to_async
def _prepare_read(reader_id: int, slug: str):
    book = get_book(None, slug)
    if not book:
        return None
    state = start_book(None, reader_id, book["id"])
    return _build_chapter_delivery(state, book, state.today_position, channel="telegram")


async def cmd_read(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    slug = " ".join(context.args).strip() if context.args else ""
    if not slug:
        await update.message.reply_text("Use /read <book-slug>. See /books.")
        return
    prepared = await _prepare_read(reader.id, slug)
    if prepared is None:
        await update.message.reply_text("No book with that name. See /books.")
        return
    await _dispatch_prepared(update, prepared)


@sync_to_async
def _prepare_begin(reader_id: int):
    plan = active_plan_for_reader(None, reader_id)
    books = list_books()
    if plan:
        book = get_book(None, plan["slug"])
        state = start_book(None, reader_id, plan["book_id"])
        return ("ok", _build_chapter_delivery(state, book, state.today_position, channel="telegram"))
    tarasov = next((b for b in books if b["profile"] == "tarasov"), None)
    chosen = tarasov or next(
        (b for b in books if b["slug"] == "slow-reading"),
        books[0] if books else None,
    )
    if not chosen:
        return ("empty", None)
    state = start_book(None, reader_id, chosen["id"])
    return ("ok", _build_chapter_delivery(state, chosen, state.today_position, channel="telegram"))


async def cmd_begin(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    status, prepared = await _prepare_begin(reader.id)
    if status == "empty":
        await update.message.reply_text("Send your .mobi of Технология жизни first.")
        return
    await _dispatch_prepared(update, prepared)


async def cmd_today(update, context) -> None:
    await _reply_position(update, offset=0)


async def cmd_yesterday(update, context) -> None:
    await _reply_position(update, offset=-1)


@sync_to_async
def _get_progress_text(reader_id: int) -> str:
    plan = active_plan_for_reader(None, reader_id)
    if not plan:
        return "No active book. Use /books then /read <slug>."
    state = state_for_plan(None, plan)
    status = "paused" if state.status == "paused" else "reading"
    return (
        f"{plan['book_title']}\n"
        f"{status}: day {state.unlocked_through + 1} of {state.total}\n"
        f"Delivery at {state.delivery_hhmm} ({state.timezone})"
    )


async def cmd_progress(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    text = await _get_progress_text(reader.id)
    await update.message.reply_text(text)


@sync_to_async
def _do_pause(reader_id: int) -> bool:
    plan = active_plan_for_reader(None, reader_id)
    if not plan:
        return False
    pause_plan(None, plan["id"])
    return True


async def cmd_pause(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    if not await _do_pause(reader.id):
        await update.message.reply_text("Nothing to pause.")
        return
    await update.message.reply_text("Paused. The calendar will not advance until /resume.")


@sync_to_async
def _do_resume(reader_id: int) -> bool:
    plan = active_plan_for_reader(None, reader_id)
    if not plan:
        return False
    resume_plan(None, plan["id"])
    return True


async def cmd_resume(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    if not await _do_resume(reader.id):
        await update.message.reply_text("Nothing to resume.")
        return
    await update.message.reply_text("Resumed. Today's chapter is the one you left on.")


async def cmd_time(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    value = " ".join(context.args).strip() if context.args else ""
    if not value:
        await update.message.reply_text("Example: /time 08:00")
        return
    await sync_to_async(update_reader)(None, reader.id, delivery_hhmm=value)
    await update.message.reply_text(f"Daily delivery set to {value}.")


async def cmd_tz(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    value = " ".join(context.args).strip() if context.args else ""
    if not value:
        await update.message.reply_text("Example: /tz Europe/Moscow  or  /tz Asia/Yerevan")
        return
    try:
        ZoneInfo(value)
    except Exception:
        await update.message.reply_text("Unknown timezone. Use an IANA name like Europe/Moscow.")
        return
    await sync_to_async(update_reader)(None, reader.id, timezone=value)
    await update.message.reply_text(f"Timezone set to {value}.")


@sync_to_async
def _ingest_and_start_for_reader(name: str, data: bytes, reader):
    path = save_upload(name, data)
    book = ingest_file(path, uploaded_by=reader, allow_replace=False)
    start_book(None, reader.id, book["id"])
    chapter = get_chapter(None, book["id"], 0)
    first_title = chapter["title"] if chapter else "—"
    return book, first_title


async def on_document(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
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
    try:
        book, first = await _ingest_and_start_for_reader(name, bytes(data), reader)
        await update.message.reply_text(
            f"Ready: {book['title']}\n"
            f"{book['chapter_count']} short chapters — one per day.\n"
            f"First: {first}\n\n"
            "Send /today to read the first one now."
        )
    except Exception as exc:
        await update.message.reply_text(f"Could not split that file: {exc}")


@sync_to_async
def _prepare_reply_position(reader_id: int, offset: int):
    plan = active_plan_for_reader(None, reader_id)
    if not plan:
        return ("err", "Start with /begin, or send the book file.")
    state = state_for_plan(None, plan)
    if state.unlocked_through < 0:
        return ("err", "The plan has not started yet. /begin")
    position = state.unlocked_through + offset
    if position < 0:
        return ("err", "That is before the beginning of the book.")
    if position > state.unlocked_through:
        return ("err", "Tomorrow's chapter is still locked.")
    book = {"id": plan["book_id"], "title": plan["book_title"], "slug": plan["slug"]}
    return ("ok", _build_chapter_delivery(state, book, position, channel="telegram-request"))


async def _reply_position(update, offset: int) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    status, payload = await _prepare_reply_position(reader.id, offset)
    if status == "err":
        await update.message.reply_text(payload)
        return
    await _dispatch_prepared(update, payload)


def _build_chapter_delivery(state, book, position: int | None, channel: str):
    if position is None:
        return ("err", "Could not start the book.")
    book_id = book["id"]
    keys = book.keys()
    book_title = book["book_title"] if "book_title" in keys else book["title"]
    chapter = get_chapter(None, book_id, position)
    if chapter is None:
        return ("err", "Chapter missing.")
    nxt = get_chapter(None, book_id, position + 1)
    messages = format_chapter_messages(
        day=position + 1,
        total=state.total or chapter_count(None, book_id),
        title=chapter["title"],
        part=chapter["part"],
        body=chapter["body"],
        next_title=nxt["title"] if nxt else None,
        book_title=book_title,
    )
    return ("messages", (messages, state.plan_id, state.today.isoformat(), position, channel))


async def _dispatch_prepared(update, prepared) -> None:
    kind, data = prepared
    if kind == "err":
        await update.message.reply_text(data)
        return
    messages, plan_id, local_date, position, channel = data
    for text in messages:
        await update.message.reply_text(text, parse_mode="HTML")
    if channel.startswith("telegram"):
        await sync_to_async(record_delivery)(None, plan_id, local_date, position, channel)


@sync_to_async
def _collect_due_deliveries(now: datetime | None = None):
    plans = list_active_plans()
    due = []
    for plan in plans:
        state = state_for_plan(None, plan, now)
        if not due_for_delivery(state, now):
            continue
        if already_delivered(None, state.plan_id, state.today.isoformat(), "telegram"):
            continue
        position = state.today_position
        if position is None:
            continue
        chapter = get_chapter(None, plan["book_id"], position)
        if chapter is None:
            continue
        nxt = get_chapter(None, plan["book_id"], position + 1)
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
    return due


async def send_due_chapters(bot, now: datetime | None = None) -> int:
    due = await _collect_due_deliveries(now)
    sent = 0
    for telegram_id, state, messages, position in due:
        for text in messages:
            await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
        await sync_to_async(record_delivery)(
            None,
            state.plan_id,
            state.today.isoformat(),
            position,
            "telegram",
        )
        sent += 1
    return sent
