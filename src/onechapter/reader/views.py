from __future__ import annotations

import logging

from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from onechapter.bot.runtime import runtime as telegram_runtime
from onechapter.db import db
from onechapter.library import (
    active_plan_for_reader,
    add_reflection,
    bot_token,
    chapter_views,
    get_book,
    get_chapter,
    get_or_create_local_reader,
    ingest_file,
    list_books,
    pause_plan,
    reflections_for,
    resume_plan,
    save_upload,
    set_bot_token,
    start_book,
    state_for_plan,
    update_reader,
)

log = logging.getLogger(__name__)


class SeeOther(HttpResponseRedirect):
    """POST endpoints answer 303 so a refresh does not submit the form again."""

    status_code = 303


def _chapter_lang(plan) -> str:
    author = (plan["author"] if plan is not None else "") or ""
    if "Тарасов" in author:
        return "ru"
    return "en"


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) < 10:
        return "••••"
    return token[:6] + "…" + token[-4:]


@require_GET
def today(request):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        plan = active_plan_for_reader(conn, reader["id"])
        books = list_books(conn)
        if not plan:
            return render(
                request,
                "today.html",
                {"plan": None, "chapter": None, "state": None, "books": books, "notes": []},
            )
        state = state_for_plan(conn, plan)
        chapter = None
        nxt = None
        notes = []
        if state.today_position is not None:
            chapter = get_chapter(conn, plan["book_id"], state.today_position)
            nxt = get_chapter(conn, plan["book_id"], state.today_position + 1)
            notes = reflections_for(conn, state.plan_id, state.today_position)
        return render(
            request,
            "today.html",
            {
                "plan": plan,
                "state": state,
                "chapter": chapter,
                "nxt": nxt,
                "books": books,
                "notes": notes,
                "chapter_lang": _chapter_lang(plan),
            },
        )


@require_GET
def library(request):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        books = list_books(conn)
        plan = active_plan_for_reader(conn, reader["id"])
        return render(
            request,
            "library.html",
            {"books": books, "plan": plan, "notice": request.GET.get("notice")},
        )


@require_GET
def book_page(request, slug: str):
    with db() as conn:
        book = get_book(conn, slug)
        if not book:
            return HttpResponseRedirect("/library")
        reader = get_or_create_local_reader(conn)
        plan_row = conn.execute(
            """
            SELECT p.*, r.timezone, r.delivery_hhmm, r.telegram_id, r.name AS reader_name,
                   b.slug, b.title AS book_title, b.author
            FROM plans p
            JOIN readers r ON r.id = p.reader_id
            JOIN books b ON b.id = p.book_id
            WHERE p.reader_id = ? AND p.book_id = ?
            """,
            (reader["id"], book["id"]),
        ).fetchone()
        state = state_for_plan(conn, plan_row) if plan_row else None
        views = chapter_views(conn, book["id"], state)
        return render(
            request,
            "book.html",
            {"book": book, "chapters": views, "state": state, "plan": plan_row},
        )


@require_POST
def start(request, slug: str):
    with db() as conn:
        book = get_book(conn, slug)
        reader = get_or_create_local_reader(conn)
        start_book(conn, reader["id"], book["id"])
    return SeeOther("/")


@require_POST
def pause(request, slug: str):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        book = get_book(conn, slug)
        plan = conn.execute(
            "SELECT id FROM plans WHERE reader_id = ? AND book_id = ?",
            (reader["id"], book["id"]),
        ).fetchone()
        if plan:
            pause_plan(conn, plan["id"])
    return SeeOther(f"/book/{slug}")


@require_POST
def resume(request, slug: str):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        book = get_book(conn, slug)
        plan = conn.execute(
            "SELECT id FROM plans WHERE reader_id = ? AND book_id = ?",
            (reader["id"], book["id"]),
        ).fetchone()
        if plan:
            resume_plan(conn, plan["id"])
    return SeeOther(f"/book/{slug}")


@require_GET
def read_chapter(request, slug: str, position: int):
    with db() as conn:
        book = get_book(conn, slug)
        if not book:
            return HttpResponseRedirect("/library")
        reader = get_or_create_local_reader(conn)
        plan_row = conn.execute(
            """
            SELECT p.*, r.timezone, r.delivery_hhmm
            FROM plans p JOIN readers r ON r.id = p.reader_id
            WHERE p.reader_id = ? AND p.book_id = ?
            """,
            (reader["id"], book["id"]),
        ).fetchone()
        state = state_for_plan(conn, plan_row) if plan_row else None
        if not state or position > state.unlocked_through:
            return HttpResponseRedirect(f"/book/{slug}")
        chapter = get_chapter(conn, book["id"], position)
        nxt = get_chapter(conn, book["id"], position + 1)
        prev = get_chapter(conn, book["id"], position - 1) if position > 0 else None
        notes = reflections_for(conn, state.plan_id, position)
        return render(
            request,
            "read.html",
            {
                "book": book,
                "chapter": chapter,
                "state": state,
                "nxt": nxt if nxt and position + 1 <= state.unlocked_through else None,
                "prev": prev,
                "notes": notes,
            },
        )


@require_POST
def add_note(request, slug: str, position: int):
    body = request.POST.get("body", "")
    with db() as conn:
        book = get_book(conn, slug)
        reader = get_or_create_local_reader(conn)
        plan_row = conn.execute(
            "SELECT id FROM plans WHERE reader_id = ? AND book_id = ?",
            (reader["id"], book["id"]),
        ).fetchone()
        if plan_row and body.strip():
            add_reflection(conn, plan_row["id"], position, body)
    return SeeOther(f"/read/{slug}/{position}")


@require_POST
def upload(request):
    upload_file = request.FILES.get("file")
    if upload_file is None:
        return SeeOther("/library?notice=empty")
    data = upload_file.read()
    if not data:
        return SeeOther("/library?notice=empty")
    path = save_upload(upload_file.name or "book.mobi", data)
    try:
        with db() as conn:
            book = ingest_file(path, conn)
        return SeeOther(f"/book/{book['slug']}")
    except Exception:
        log.exception("Ingest failed")
        return SeeOther("/library?notice=fail")


@require_http_methods(["GET", "POST"])
async def settings_page(request):
    if request.method == "POST":
        return await _save_settings(request)
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        token = bot_token(conn)
        return render(
            request,
            "settings.html",
            {
                "reader": reader,
                "token_masked": _mask(token),
                "has_token": bool(token),
                "notice": request.GET.get("notice"),
            },
        )


async def _save_settings(request):
    # Awaited on the server event loop so polling keeps running after this response.
    timezone = request.POST.get("timezone", "")
    delivery_hhmm = request.POST.get("delivery_hhmm", "")
    token = request.POST.get("telegram_bot_token", "").strip()
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        update_reader(
            conn,
            reader["id"],
            timezone=timezone.strip(),
            delivery_hhmm=delivery_hhmm.strip(),
        )
        if token:
            set_bot_token(conn, token)
    if token:
        try:
            await telegram_runtime.start(token)
        except ValueError:
            return SeeOther("/settings?notice=bad_token")
        return SeeOther("/settings?notice=started")
    return SeeOther("/settings?notice=saved")


@require_GET
def health(request):
    snap = telegram_runtime.snapshot()
    return JsonResponse({"ok": True, "bot": snap["running"], "username": snap["username"]})
