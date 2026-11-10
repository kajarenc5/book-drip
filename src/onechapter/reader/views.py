from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.http import HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from onechapter.auth import verify_magic_login_token, verify_telegram_login_data
from onechapter.bot.runtime import runtime as telegram_runtime
from onechapter.config import env_bot_username
from onechapter.library import (
    active_plan_for_reader,
    add_reflection,
    bot_token,
    chapter_views,
    cleanup_staged_upload,
    get_book,
    get_chapter,
    get_or_create_telegram_reader,
    get_plan,
    ingest_file,
    list_books,
    pause_plan,
    reflections_for,
    resolve_web_reader,
    resume_plan,
    save_upload,
    set_bot_token,
    start_book,
    state_for_plan,
    update_reader,
)
from onechapter.reader.models import Reader

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


def _require_reader_or_redirect(request):
    reader = resolve_web_reader(request)
    if reader is None:
        return None, HttpResponseRedirect("/login")
    if not reader.is_allowed:
        return None, HttpResponseForbidden("One Chapter is currently invite-only.")
    return reader, None


@require_GET
def login_page(request):
    invite = request.GET.get("invite", "").strip()
    if invite:
        request.session["pending_invite_code"] = invite
    snap = telegram_runtime.snapshot()
    return render(
        request,
        "login.html",
        {
            "bot_username": snap["username"] or env_bot_username() or None,
            "notice": request.GET.get("notice"),
        },
    )


@require_http_methods(["GET", "POST"])
def telegram_auth(request):
    params = request.POST if request.method == "POST" else request.GET
    token = bot_token()
    verified = verify_telegram_login_data(params, token)
    if not verified:
        return SeeOther("/login?notice=invalid")
    tg_id = verified["id"]
    first = verified.get("first_name", "")
    last = verified.get("last_name", "")
    full_name = f"{first} {last}".strip() or verified.get("username", "")
    handle = verified.get("username", "")
    invite = request.session.pop("pending_invite_code", None)
    reader = get_or_create_telegram_reader(
        None,
        tg_id,
        full_name or None,
        handle=handle or None,
        invite_code=invite,
    )
    if not reader.is_allowed:
        return SeeOther("/login?notice=invite_only")
    if reader.user:
        auth_login(request, reader.user)
    return SeeOther("/")


@require_GET
def magic_auth(request):
    token = request.GET.get("token", "").strip()
    payload = verify_magic_login_token(token)
    if not payload:
        return SeeOther("/login?notice=expired")
    reader = Reader.objects.select_related("user").filter(id=payload["reader_id"]).first()
    if not reader or not reader.is_allowed:
        return SeeOther("/login?notice=invite_only")
    if reader.user:
        auth_login(request, reader.user)
    return SeeOther("/")


@require_POST
def logout_view(request):
    auth_logout(request)
    return SeeOther("/login")


@require_GET
def today(request):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    plan = active_plan_for_reader(None, reader["id"])
    books = list_books()
    if not plan:
        return render(
            request,
            "today.html",
            {"plan": None, "chapter": None, "state": None, "books": books, "notes": []},
        )
    state = state_for_plan(None, plan)
    chapter = None
    nxt = None
    notes = []
    if state.today_position is not None:
        chapter = get_chapter(None, plan["book_id"], state.today_position)
        nxt = get_chapter(None, plan["book_id"], state.today_position + 1)
        notes = reflections_for(None, state.plan_id, state.today_position)
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
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    books = list_books()
    plan = active_plan_for_reader(None, reader["id"])
    return render(
        request,
        "library.html",
        {"books": books, "plan": plan, "notice": request.GET.get("notice")},
    )


@require_GET
def book_page(request, slug: str):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    book = get_book(None, slug)
    if not book:
        return HttpResponseRedirect("/library")
    plan_row = get_plan(None, reader["id"], book["id"])
    state = state_for_plan(None, plan_row) if plan_row else None
    views = chapter_views(None, book["id"], state)
    return render(
        request,
        "book.html",
        {"book": book, "chapters": views, "state": state, "plan": plan_row},
    )


@require_POST
def start(request, slug: str):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    book = get_book(None, slug)
    if not book:
        return SeeOther("/library")
    start_book(None, reader["id"], book["id"])
    return SeeOther("/")


@require_POST
def pause(request, slug: str):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    book = get_book(None, slug)
    if book:
        plan = get_plan(None, reader["id"], book["id"])
        if plan:
            pause_plan(None, plan["id"])
    return SeeOther(f"/book/{slug}")


@require_POST
def resume(request, slug: str):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    book = get_book(None, slug)
    if book:
        plan = get_plan(None, reader["id"], book["id"])
        if plan:
            resume_plan(None, plan["id"])
    return SeeOther(f"/book/{slug}")


@require_GET
def read_chapter(request, slug: str, position: int):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    book = get_book(None, slug)
    if not book:
        return HttpResponseRedirect("/library")
    plan_row = get_plan(None, reader["id"], book["id"])
    state = state_for_plan(None, plan_row) if plan_row else None
    if not state or position > state.unlocked_through:
        return HttpResponseRedirect(f"/book/{slug}")
    chapter = get_chapter(None, book["id"], position)
    nxt = get_chapter(None, book["id"], position + 1)
    prev = get_chapter(None, book["id"], position - 1) if position > 0 else None
    notes = reflections_for(None, state.plan_id, position)
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
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    body = request.POST.get("body", "")
    book = get_book(None, slug)
    if book:
        plan_row = get_plan(None, reader["id"], book["id"])
        if plan_row and body.strip():
            add_reflection(None, plan_row["id"], position, body, source_channel="web")
    return SeeOther(f"/read/{slug}/{position}")


@require_POST
def upload(request):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    upload_file = request.FILES.get("file")
    if upload_file is None:
        return SeeOther("/library?notice=empty")
    data = upload_file.read()
    if not data:
        return SeeOther("/library?notice=empty")
    path = save_upload(upload_file.name or "book.mobi", data)
    try:
        book = ingest_file(path, uploaded_by=reader, allow_replace=False)
        return SeeOther(f"/book/{book['slug']}")
    except Exception:
        log.exception("Ingest failed")
        return SeeOther("/library?notice=fail")
    finally:
        cleanup_staged_upload(path)


@sync_to_async
def _load_settings_context(request):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return None, redirect_resp
    token = bot_token()
    return (
        {
            "reader": reader,
            "token_masked": _mask(token) if (reader.is_operator or settings.DEBUG) else "",
            "has_token": bool(token),
            "can_edit_token": bool(reader.is_operator or settings.DEBUG),
            "notice": request.GET.get("notice"),
        },
        None,
    )


@sync_to_async
def _apply_settings_update(request, timezone: str, delivery_hhmm: str, token: str):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return None, redirect_resp, False
    update_reader(
        None,
        reader["id"],
        timezone=timezone.strip(),
        delivery_hhmm=delivery_hhmm.strip(),
    )
    can_set_token = bool(reader.is_operator or settings.DEBUG)
    if token and can_set_token:
        set_bot_token(None, token)
    return reader, None, (bool(token) and can_set_token)


@require_http_methods(["GET", "POST"])
async def settings_page(request):
    if request.method == "POST":
        return await _save_settings(request)
    ctx, redirect_resp = await _load_settings_context(request)
    if redirect_resp:
        return redirect_resp
    return await sync_to_async(render)(request, "settings.html", ctx)


async def _save_settings(request):
    # Awaited on the server event loop so polling keeps running after this response.
    timezone = request.POST.get("timezone", "")
    delivery_hhmm = request.POST.get("delivery_hhmm", "")
    token = request.POST.get("telegram_bot_token", "").strip()
    _, redirect_resp, should_start_bot = await _apply_settings_update(
        request, timezone, delivery_hhmm, token
    )
    if redirect_resp:
        return redirect_resp
    if should_start_bot:
        try:
            await telegram_runtime.start(token)
        except ValueError:
            return SeeOther("/settings?notice=bad_token")
        return SeeOther("/settings?notice=started")
    return SeeOther("/settings?notice=saved")


@require_GET
def health(request):
    if not settings.DEBUG:
        return JsonResponse({"ok": True})
    snap = telegram_runtime.snapshot()
    return JsonResponse({"ok": True, "bot": snap["running"], "username": snap["username"]})
