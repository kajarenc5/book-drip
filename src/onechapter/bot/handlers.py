from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from telegram.error import BadRequest, Forbidden, RetryAfter

from onechapter.auth import create_magic_login_token
from onechapter.config import (
    access_mode,
    max_reflection_chars,
    public_url,
    telegram_max_upload_bytes,
)
from onechapter.ingest.extract import SUPPORTED_SUFFIXES, UploadValidationError
from onechapter.library import (
    active_plan_for_reader,
    add_reflection,
    chapter_count,
    check_upload_rate_limit,
    cleanup_staged_upload,
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
    set_telegram_delivery_enabled,
    start_book,
    state_for_plan,
    update_reader,
)
from onechapter.reading import due_for_delivery, normalize_hhmm
from onechapter.telegram_format import format_chapter_messages

log = logging.getLogger(__name__)

HELP = """One chapter a day. No more.

/today — reread today's chapter
/yesterday — the previous one
/progress — where you are
/pause and /resume — freeze the calendar
/stop — turn off daily Telegram messages
/time 08:00 — morning delivery
/tz Europe/Moscow — your timezone
/books — library
/begin — start (or restart) the current book
/login — open the web reader signed in as you

Write any note in this chat to save a reflection on today's chapter.
Send an .epub, .mobi, .fb2, or .txt file to add a book to the library.
"""

INVITE_ONLY_MESSAGE = (
    "One Chapter is currently in invite-only mode.\n\n"
    "Ask the operator for an invite link (/start inv_<code>) to begin reading."
)

BLOCKED_ACCOUNT_MESSAGE = "Access to One Chapter has been disabled for this account."


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
    user_inactive = bool(reader.user_id and reader.user and not reader.user.is_active)
    if reader.is_blocked or user_inactive:
        await update.message.reply_text(BLOCKED_ACCOUNT_MESSAGE)
        return None
    if not reader.is_allowed:
        msg = BLOCKED_ACCOUNT_MESSAGE if access_mode() == "public" else INVITE_ONLY_MESSAGE
        await update.message.reply_text(msg)
        return None
    return reader


async def cmd_start(update, context) -> None:
    arg = context.args[0].strip() if getattr(context, "args", None) else None
    invite_arg = None if arg == "connect" else arg
    reader = await _require_allowed_reader(update, invite_code=invite_arg)
    if not reader:
        return
    await sync_to_async(set_telegram_delivery_enabled)(reader.id, enabled=True)
    await update.message.reply_text(
        "This is a slow-reading companion.\n\n"
        "It will send you one short, dense chapter each day, then wait.\n\n" + HELP
    )


async def cmd_stop(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    await sync_to_async(set_telegram_delivery_enabled)(
        reader.id,
        enabled=False,
        reason="user_stopped",
    )
    await update.message.reply_text(
        "Daily Telegram delivery is off. You can still read in the web reader, "
        "or send /start or /resume anytime to turn daily messages back on."
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
        await update.message.reply_text("The library is empty. Send an .epub, .mobi, .fb2, or .txt file to begin.")
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
    slow = next((b for b in books if b["slug"] == "slow-reading"), None)
    chosen = slow or (books[0] if books else None)
    if not chosen:
        return ("empty", None)
    state = start_book(None, reader_id, chosen["id"])
    return ("ok", _build_chapter_delivery(state, chosen, state.today_position, channel="telegram"))


async def cmd_begin(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    slug = " ".join(context.args).strip() if getattr(context, "args", None) else ""
    if slug:
        prepared_slug = await _prepare_read(reader.id, slug)
        if prepared_slug is None:
            await update.message.reply_text("No book with that name. See /books.")
            return
        await _dispatch_prepared(update, prepared_slug)
        return
    status, prepared = await _prepare_begin(reader.id)
    if status == "empty":
        await update.message.reply_text(
            "The library is empty. Upload an .epub, .mobi, .fb2, or .txt book first."
        )
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
def _do_resume(reader_id: int) -> str:
    delivery_re_enabled = set_telegram_delivery_enabled(reader_id, enabled=True)
    plan = active_plan_for_reader(None, reader_id)
    if plan and plan["status"] == "paused":
        resume_plan(None, plan["id"])
        return "plan"
    if delivery_re_enabled:
        return "delivery"
    if plan:
        return "already_active"
    return "nothing"


async def cmd_resume(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    outcome = await _do_resume(reader.id)
    if outcome == "nothing":
        await update.message.reply_text("Nothing to resume.")
        return
    if outcome == "delivery":
        await update.message.reply_text("Resumed. Daily Telegram delivery is back on.")
        return
    await update.message.reply_text("Resumed. Today's chapter is the one you left on.")


async def cmd_time(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    value = " ".join(context.args).strip() if context.args else ""
    normalized = normalize_hhmm(value)
    if not normalized:
        await update.message.reply_text("Example: /time 08:00")
        return
    await sync_to_async(update_reader)(None, reader.id, delivery_hhmm=normalized)
    await update.message.reply_text(f"Daily delivery set to {normalized}.")


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
    try:
        book = ingest_file(path, uploaded_by=reader, allow_replace=False)
        start_book(None, reader.id, book["id"])
        chapter = get_chapter(None, book["id"], 0)
        first_title = chapter["title"] if chapter else "—"
        return book, first_title
    finally:
        cleanup_staged_upload(path)


def _telegram_size_limit_message(limit_bytes: int) -> str:
    mb = limit_bytes / (1024 * 1024)
    if mb >= 1:
        label = f"{int(mb)} MB" if mb.is_integer() else f"{mb:.1f} MB"
    else:
        label = f"{limit_bytes} bytes"
    return (
        f"That file is too large for Telegram (maximum {label}). "
        "Upload larger books (up to 25 MB) in the web reader."
    )


async def on_document(update, context) -> None:
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    doc = getattr(update.message, "document", None)
    if doc is None:
        return

    try:
        check_upload_rate_limit(reader_id=reader.id)
    except UploadValidationError as exc:
        await update.message.reply_text(exc.user_message)
        return

    name = (doc.file_name or "").strip()
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix not in SUPPORTED_SUFFIXES:
        await update.message.reply_text(
            "Unsupported file format. Send a .mobi, .azw, .azw3, .epub, .fb2, .html, .txt, or .md file."
        )
        return

    limit = telegram_max_upload_bytes()
    file_size = getattr(doc, "file_size", None)
    if file_size is not None and file_size <= 0:
        await update.message.reply_text("That file is empty.")
        return
    if file_size is not None and file_size > limit:
        await update.message.reply_text(_telegram_size_limit_message(limit))
        return

    await update.message.reply_text("Reading the file and cutting it into daily chapters…")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
    except Exception as exc:
        if "file is too big" in str(exc).lower() or "too large" in str(exc).lower():
            await update.message.reply_text(_telegram_size_limit_message(limit))
            return
        log.warning("Could not download Telegram document for reader=%s: %s", reader.id, exc)
        await update.message.reply_text("Could not download that file from Telegram. Please try again.")
        return

    if not data:
        await update.message.reply_text("That file is empty.")
        return
    if len(data) > limit:
        await update.message.reply_text(_telegram_size_limit_message(limit))
        return

    try:
        book, first = await _ingest_and_start_for_reader(name, bytes(data), reader)
        await update.message.reply_text(
            f"Ready: {book['title']}\n"
            f"{book['chapter_count']} short chapters — one per day.\n"
            f"First: {first}\n\n"
            "Send /today to read the first one now."
        )
    except UploadValidationError as exc:
        log.warning("Rejected Telegram document %r for reader=%s (%s): %s", name, reader.id, exc.code, exc)
        await update.message.reply_text(f"Could not import that file: {exc.user_message}")
    except Exception:
        log.exception("Unexpected error during Telegram document ingest")
        await update.message.reply_text(
            "Could not process that file. Make sure it is a valid, unencrypted book file."
        )


@sync_to_async
def _save_telegram_reflection(reader_id: int, body: str):
    plan = active_plan_for_reader(None, reader_id)
    if not plan:
        return ("no_plan", None)
    state = state_for_plan(None, plan)
    position = state.today_position if state.today_position is not None else state.unlocked_through
    if position is None or position < 0:
        return ("not_started", None)
    add_reflection(None, state.plan_id, position, body, source_channel="telegram")
    return ("ok", (plan["book_title"], position + 1))


async def on_text_reflection(update, context) -> None:
    if not update.message or not update.message.text:
        return
    body = update.message.text.strip()
    if not body:
        return
    reader = await _require_allowed_reader(update)
    if not reader:
        return
    limit = max_reflection_chars()
    if len(body) > limit:
        await update.message.reply_text(
            f"That note is too long (maximum {limit:,} characters). Keep a shorter passage or reflection."
        )
        return
    status, info = await _save_telegram_reflection(reader.id, body)
    if status != "ok" or not info:
        await update.message.reply_text("Start a book with /begin first to save chapter notes.")
        return
    book_title, day = info
    await update.message.reply_text(f"Saved note on day {day} ({book_title}).")


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


_PERMANENT_BAD_REQUEST_PHRASES = (
    "chat not found",
    "user not found",
    "peer_id_invalid",
    "chat_id is empty",
    "user is deactivated",
    "bot was blocked",
    "bot can't initiate conversation",
    "have no rights to send a message",
)
_MAX_TRACKED_PARTIAL_DELIVERIES = 4_096
_partial_chunk_progress: dict[tuple[int, str], int] = {}
_retry_after_until_ts: float = 0.0


def reset_delivery_runtime_state() -> None:
    global _retry_after_until_ts
    _partial_chunk_progress.clear()
    _retry_after_until_ts = 0.0


def _is_permanent_chat_error(exc: BaseException) -> bool:
    if isinstance(exc, Forbidden):
        return True
    if isinstance(exc, BadRequest):
        msg = str(exc).lower()
        return any(phrase in msg for phrase in _PERMANENT_BAD_REQUEST_PHRASES)
    return False


def _extract_retry_after_seconds(exc: RetryAfter) -> float:
    raw = getattr(exc, "_retry_after", None)
    if raw is None:
        raw = exc.retry_after
    if isinstance(raw, timedelta):
        return max(0.0, raw.total_seconds())
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


@sync_to_async
def _collect_due_deliveries(now: datetime | None = None):
    from onechapter.reader.models import Chapter, Delivery

    plans = list_active_plans()
    if not plans:
        return []

    candidates = []
    for plan in plans:
        try:
            state = state_for_plan(None, plan, now)
            if not due_for_delivery(state, now):
                continue
            if state.today_position is None:
                continue
            candidates.append((plan, state))
        except Exception:
            log.exception("Failed to evaluate plan state for plan %s", getattr(plan, "id", None))

    if not candidates:
        return []

    candidate_plan_ids = [state.plan_id for _, state in candidates]
    candidate_dates = {state.today for _, state in candidates}
    delivered_pairs = set(
        Delivery.objects.filter(
            plan_id__in=candidate_plan_ids,
            local_date__in=candidate_dates,
            channel="telegram",
        ).values_list("plan_id", "local_date")
    )

    pending = [
        (plan, state)
        for plan, state in candidates
        if (state.plan_id, state.today) not in delivered_pairs
    ]
    if not pending:
        return []

    due_book_ids = {plan["book_id"] for plan, _ in pending}
    needed_positions: set[int] = set()
    for _, state in pending:
        assert state.today_position is not None
        needed_positions.add(state.today_position)
        needed_positions.add(state.today_position + 1)

    chapters_by_book_pos = {
        (ch.book_id, ch.position): ch
        for ch in Chapter.objects.filter(
            book_id__in=due_book_ids,
            position__in=needed_positions,
        )
    }

    due = []
    for plan, state in pending:
        try:
            position = state.today_position
            if position is None:
                continue
            chapter = chapters_by_book_pos.get((plan["book_id"], position))
            if chapter is None:
                continue
            nxt = chapters_by_book_pos.get((plan["book_id"], position + 1))
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
        except Exception:
            log.exception("Failed to prepare due delivery for plan %s", getattr(plan, "id", None))
    return due


async def send_due_chapters(bot, now: datetime | None = None) -> int:
    global _retry_after_until_ts

    effective_ts = now.timestamp() if now is not None else time.time()
    if effective_ts < _retry_after_until_ts:
        return 0

    due = await _collect_due_deliveries(now)
    if len(_partial_chunk_progress) > _MAX_TRACKED_PARTIAL_DELIVERIES:
        active_keys = {(state.plan_id, state.today.isoformat()) for _, state, _, _ in due}
        for stale_key in list(_partial_chunk_progress):
            if stale_key not in active_keys:
                _partial_chunk_progress.pop(stale_key, None)

    sent = 0
    for telegram_id, state, messages, position in due:
        local_date_iso = state.today.isoformat()
        progress_key = (state.plan_id, local_date_iso)
        start_idx = _partial_chunk_progress.get(progress_key, 0)
        if start_idx >= len(messages):
            start_idx = 0
        try:
            for idx in range(start_idx, len(messages)):
                await bot.send_message(
                    chat_id=telegram_id,
                    text=messages[idx],
                    parse_mode="HTML",
                )
                _partial_chunk_progress[progress_key] = idx + 1
            _partial_chunk_progress.pop(progress_key, None)
            await sync_to_async(record_delivery)(
                None,
                state.plan_id,
                local_date_iso,
                position,
                "telegram",
            )
            sent += 1
        except RetryAfter as exc:
            delay = _extract_retry_after_seconds(exc)
            _retry_after_until_ts = effective_ts + delay
            log.warning(
                "Telegram RetryAfter (%.1fs) while delivering to telegram_id=%s (plan=%s); deferring remaining batch",
                delay,
                telegram_id,
                state.plan_id,
            )
            break
        except (Forbidden, BadRequest) as exc:
            if _is_permanent_chat_error(exc):
                _partial_chunk_progress.pop(progress_key, None)
                await sync_to_async(set_telegram_delivery_enabled)(
                    state.reader_id,
                    telegram_id=telegram_id,
                    enabled=False,
                    reason="telegram_unreachable",
                )
                log.warning(
                    "Disabled Telegram delivery for unreachable chat telegram_id=%s (plan=%s): %s",
                    telegram_id,
                    state.plan_id,
                    exc,
                )
            else:
                log.warning(
                    "Could not deliver chapter to telegram_id=%s (plan=%s)",
                    telegram_id,
                    state.plan_id,
                    exc_info=True,
                )
        except Exception:
            log.warning(
                "Could not deliver chapter to telegram_id=%s (plan=%s)",
                telegram_id,
                state.plan_id,
                exc_info=True,
            )
    return sent
