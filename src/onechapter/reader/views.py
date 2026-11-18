from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from onechapter.auth import verify_magic_login_token, verify_telegram_login_data
from onechapter.bot.runtime import runtime as telegram_runtime
from onechapter.config import (
    DEFAULT_TIMEZONE,
    access_mode,
    allowed_telegram_ids,
    env_bot_username,
    invite_code as configured_invite_code,
    is_access_allowed,
    is_local_single_user_dev,
    max_reflection_chars,
    max_upload_bytes,
    operator_telegram_ids,
)
from onechapter.ingest.extract import SUPPORTED_SUFFIXES, UploadValidationError
from onechapter.ingest.textutil import detect_language, normalize_lang_code
from onechapter.library import (
    _ensure_django_user,
    active_plan_for_reader,
    add_reflection,
    bot_token,
    chapter_views,
    check_upload_rate_limit,
    cleanup_staged_upload,
    get_book,
    get_chapter,
    get_or_create_telegram_reader,
    get_plan,
    get_telegram_identity,
    ingest_file,
    list_books,
    pause_plan,
    reflections_for,
    resolve_web_reader,
    resume_plan,
    save_upload,
    set_bot_token,
    set_telegram_delivery_enabled,
    start_book,
    state_for_plan,
    update_reader,
)
from onechapter.reader.models import ChannelIdentity, Reader
from onechapter.reading import is_valid_timezone, list_iana_timezones, normalize_hhmm

log = logging.getLogger(__name__)


class SeeOther(HttpResponseRedirect):
    """POST endpoints answer 303 so a refresh does not submit the form again."""

    status_code = 303


def _chapter_lang(target) -> str:
    if target is None:
        return "en"
    book_obj = getattr(target, "book", None)
    raw_lang = None
    if book_obj is not None:
        raw_lang = getattr(book_obj, "language", None)
    if not raw_lang:
        raw_lang = getattr(target, "language", None)
    if not raw_lang and hasattr(target, "get"):
        raw_lang = target.get("language")
    norm = normalize_lang_code(raw_lang)
    if norm:
        return norm
    author = (target.get("author") if hasattr(target, "get") else getattr(target, "author", "")) or ""
    title = (
        (target.get("book_title") or target.get("title"))
        if hasattr(target, "get")
        else (getattr(target, "book_title", None) or getattr(target, "title", ""))
    ) or ""
    return detect_language(f"{title} {author}") or "en"


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) < 10:
        return "••••"
    return token[:6] + "…" + token[-4:]


def _is_reader_blocked_or_inactive(reader) -> bool:
    if reader.is_blocked:
        return True
    if reader.user_id and reader.user and not reader.user.is_active:
        return True
    return False


def _require_reader_or_redirect(request):
    reader = resolve_web_reader(request)
    if reader is None:
        return None, HttpResponseRedirect("/login")
    if _is_reader_blocked_or_inactive(reader):
        return None, HttpResponseForbidden("Access to One Chapter is disabled for this account.")
    if not reader.is_allowed:
        msg = (
            "Access to One Chapter is disabled for this account."
            if access_mode() == "public"
            else "One Chapter is currently invite-only."
        )
        return None, HttpResponseForbidden(msg)
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


def _extract_browser_timezone(request) -> str | None:
    candidates = (
        request.POST.get("tz"),
        request.GET.get("tz"),
        request.COOKIES.get("onechapter_tz"),
    )
    for raw in candidates:
        if not raw:
            continue
        cleaned = unquote(str(raw)).strip()
        if cleaned and is_valid_timezone(cleaned):
            return cleaned
    return None


@require_http_methods(["GET", "POST"])
def telegram_auth(request):
    params = request.POST if request.method == "POST" else request.GET
    auth_params = {
        k: v
        for k, v in params.items()
        if k not in {"tz", "csrfmiddlewaretoken"}
    }
    token = bot_token()
    verified = verify_telegram_login_data(auth_params, token)
    if not verified:
        return SeeOther("/login?notice=invalid")
    tg_id = verified["id"]
    first = verified.get("first_name", "")
    last = verified.get("last_name", "")
    full_name = f"{first} {last}".strip() or verified.get("username", "")
    handle = verified.get("username", "")
    invite = request.session.pop("pending_invite_code", None)
    detected_tz = _extract_browser_timezone(request)
    reader = get_or_create_telegram_reader(
        None,
        tg_id,
        full_name or None,
        handle=handle or None,
        invite_code=invite,
        timezone=detected_tz,
    )
    if _is_reader_blocked_or_inactive(reader):
        return SeeOther("/login?notice=blocked")
    if not reader.is_allowed:
        notice = "blocked" if access_mode() == "public" else "invite_only"
        return SeeOther(f"/login?notice={notice}")
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
    if not reader:
        return SeeOther("/login?notice=invite_only")

    channel = str(payload.get("channel") or "telegram")
    ext_id = str(payload.get("external_id") or "")
    ident = None
    if ext_id:
        ident = ChannelIdentity.objects.filter(
            reader=reader,
            channel=channel,
            external_id=ext_id,
        ).first()
        if ident is None:
            return SeeOther("/login?notice=invalid")

    if _is_reader_blocked_or_inactive(reader):
        return SeeOther("/login?notice=blocked")

    pending_invite = request.session.pop("pending_invite_code", None)
    if not reader.is_allowed:
        awaiting_invite = bool(ident and (ident.metadata or {}).get("awaiting_invite"))
        explicitly_invited_or_allowlisted = bool(
            (pending_invite and configured_invite_code() and is_access_allowed(ext_id or None, pending_invite))
            or (ext_id and ext_id in allowed_telegram_ids())
            or (ext_id and ext_id in operator_telegram_ids())
        )
        if is_access_allowed(ext_id or None, pending_invite) and (
            awaiting_invite or explicitly_invited_or_allowlisted
        ):
            detected_tz = _extract_browser_timezone(request)
            update_fields = ["is_allowed"]
            reader.is_allowed = True
            if detected_tz and reader.timezone == DEFAULT_TIMEZONE:
                reader.timezone = detected_tz
                update_fields.append("timezone")
            reader.save(update_fields=update_fields)
            if ident and awaiting_invite:
                meta = dict(ident.metadata or {})
                meta.pop("awaiting_invite", None)
                ident.metadata = meta
                ident.save(update_fields=["metadata"])
        else:
            notice = "blocked" if access_mode() == "public" else "invite_only"
            return SeeOther(f"/login?notice={notice}")

    user = _ensure_django_user(reader, f"{channel}-{ext_id}" if ext_id else None)
    auth_login(request, user)
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
            "max_reflection_chars": max_reflection_chars(),
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
            "chapter_lang": _chapter_lang(book),
            "notice": request.GET.get("notice"),
            "max_reflection_chars": max_reflection_chars(),
        },
    )


@require_POST
def add_note(request, slug: str, position: int):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp
    body = request.POST.get("body", "")
    cleaned = body.strip()
    if len(cleaned) > max_reflection_chars():
        return SeeOther(f"/read/{slug}/{position}?notice=note_too_long")
    book = get_book(None, slug)
    if book and cleaned:
        plan_row = get_plan(None, reader["id"], book["id"])
        if plan_row:
            add_reflection(None, plan_row["id"], position, cleaned, source_channel="web")
    return SeeOther(f"/read/{slug}/{position}")


_VALID_UPLOAD_NOTICES = frozenset(
    {
        "empty",
        "too_large",
        "unsupported",
        "unsafe",
        "corrupt",
        "no_chapters",
        "rate_limited",
        "fail",
    }
)


def _client_ip(request) -> str:
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    trusted_proxy = False
    if remote_addr:
        try:
            addr = ipaddress.ip_address(remote_addr)
            trusted_proxy = addr.is_loopback or addr.is_private
        except ValueError:
            trusted_proxy = False

    if trusted_proxy:
        cf_ip = (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip()
        if cf_ip:
            return cf_ip
        xff = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
        if xff:
            first_hop = xff.split(",")[0].strip()
            if first_hop:
                return first_hop
    return remote_addr or "unknown"


@require_POST
def upload(request):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return redirect_resp

    try:
        check_upload_rate_limit(
            reader_id=reader["id"],
            client_ip=_client_ip(request),
        )
    except UploadValidationError:
        return SeeOther("/library?notice=rate_limited")

    try:
        upload_file = request.FILES.get("file")
    except (RequestDataTooBig, MultiPartParserError):
        return SeeOther("/library?notice=too_large")

    if upload_file is None:
        return SeeOther("/library?notice=empty")

    raw_name = upload_file.name or ""
    suffix = Path(raw_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return SeeOther("/library?notice=unsupported")

    limit = max_upload_bytes()
    file_size = getattr(upload_file, "size", 0) or 0
    if file_size > limit:
        return SeeOther("/library?notice=too_large")

    try:
        data = upload_file.read(limit + 1)
    except (RequestDataTooBig, MultiPartParserError, OSError):
        return SeeOther("/library?notice=too_large")

    if not data:
        return SeeOther("/library?notice=empty")
    if len(data) > limit:
        return SeeOther("/library?notice=too_large")

    path = save_upload(raw_name, data)
    try:
        book = ingest_file(path, uploaded_by=reader, allow_replace=False)
        return SeeOther(f"/book/{book['slug']}")
    except UploadValidationError as exc:
        log.warning("Rejected uploaded file %r (%s): %s", raw_name, exc.code, exc)
        notice = exc.code if exc.code in _VALID_UPLOAD_NOTICES else "fail"
        return SeeOther(f"/library?notice={notice}")
    except Exception:
        log.exception("Unexpected error during web upload")
        return SeeOther("/library?notice=fail")
    finally:
        cleanup_staged_upload(path)


def _is_operator_reader(reader) -> bool:
    if not reader or _is_reader_blocked_or_inactive(reader) or not reader.is_allowed or not reader.is_operator:
        return False
    op_ids = operator_telegram_ids()
    if op_ids:
        tg_id = reader.telegram_id
        return tg_id is not None and str(tg_id) in op_ids
    if not is_local_single_user_dev() and access_mode() == "public":
        return False
    return True


@sync_to_async
def _load_settings_context(request):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return None, redirect_resp
    can_manage_bot = _is_operator_reader(reader)
    token = bot_token() if can_manage_bot else ""
    tg_ident = get_telegram_identity(reader["id"])
    return (
        {
            "reader": reader,
            "token_masked": _mask(token) if can_manage_bot else "",
            "has_token": bool(token),
            "can_edit_token": can_manage_bot,
            "has_telegram_channel": tg_ident is not None,
            "telegram_delivery_enabled": bool(tg_ident.is_delivery_enabled) if tg_ident else True,
            "telegram_handle": tg_ident.handle if tg_ident else "",
            "delivery_disabled_reason": (
                (tg_ident.metadata or {}).get("delivery_disabled_reason") if tg_ident else None
            ),
            "iana_timezones": list_iana_timezones(),
            "notice": request.GET.get("notice"),
        },
        None,
    )


@sync_to_async
def _apply_settings_update(
    request,
    timezone: str,
    delivery_hhmm: str,
    token: str,
    delivery_enabled: bool | None,
):
    reader, redirect_resp = _require_reader_or_redirect(request)
    if redirect_resp:
        return None, redirect_resp, False

    tz_clean = timezone.strip()
    if not tz_clean or not is_valid_timezone(tz_clean):
        return None, SeeOther("/settings?notice=bad_timezone"), False

    hhmm_clean = normalize_hhmm(delivery_hhmm.strip())
    if not hhmm_clean:
        return None, SeeOther("/settings?notice=bad_time"), False

    update_reader(
        None,
        reader["id"],
        timezone=tz_clean,
        delivery_hhmm=hhmm_clean,
        strict=True,
    )
    if delivery_enabled is not None:
        set_telegram_delivery_enabled(
            reader["id"],
            enabled=delivery_enabled,
            reason=None if delivery_enabled else "user_settings",
        )

    can_set_token = _is_operator_reader(reader)
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
    raw_toggle = request.POST.get("telegram_delivery_enabled")
    if raw_toggle is not None:
        delivery_enabled: bool | None = raw_toggle.strip().lower() in {"1", "on", "true", "yes"}
    elif "telegram_delivery_field" in request.POST:
        delivery_enabled = False
    else:
        delivery_enabled = None

    _, redirect_resp, should_start_bot = await _apply_settings_update(
        request,
        timezone,
        delivery_hhmm,
        token,
        delivery_enabled,
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
