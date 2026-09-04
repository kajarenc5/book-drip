from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from onechapter.config import public_url
from onechapter.db import db
from onechapter.library import (
    active_plan_for_reader,
    add_reflection,
    bootstrap,
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
    start_book,
    state_for_plan,
    bot_token,
    set_bot_token,
    update_reader,
)

log = logging.getLogger(__name__)
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

telegram_app = None
scheduler = None


def _nav_ctx(request: Request, **extra):
    return {"request": request, "public_url": public_url(), **extra}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app, scheduler
    bootstrap()
    token = ""
    with db() as conn:
        token = bot_token(conn)
    if token:
        from onechapter.bot.runtime import build_application, build_scheduler

        telegram_app = build_application(token)
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        scheduler = build_scheduler(telegram_app)
        scheduler.start()
        log.info("Telegram bot polling started")
    else:
        log.info("No TELEGRAM_BOT_TOKEN — web reader only")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


app = FastAPI(title="One Chapter", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


templates.env.filters["paragraphs"] = _paragraphs


@app.get("/", response_class=HTMLResponse)
async def today(request: Request):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        plan = active_plan_for_reader(conn, reader["id"])
        books = list_books(conn)
        if not plan:
            return templates.TemplateResponse(
                request,
                "today.html",
                _nav_ctx(request, plan=None, chapter=None, state=None, books=books, notes=[]),
            )
        state = state_for_plan(conn, plan)
        chapter = None
        nxt = None
        notes = []
        if state.today_position is not None:
            chapter = get_chapter(conn, plan["book_id"], state.today_position)
            nxt = get_chapter(conn, plan["book_id"], state.today_position + 1)
            notes = reflections_for(conn, state.plan_id, state.today_position)
        return templates.TemplateResponse(
            request,
            "today.html",
            _nav_ctx(
                request,
                plan=plan,
                state=state,
                chapter=chapter,
                nxt=nxt,
                books=books,
                notes=notes,
            ),
        )


@app.get("/library", response_class=HTMLResponse)
async def library(request: Request, notice: str | None = None):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        books = list_books(conn)
        plan = active_plan_for_reader(conn, reader["id"])
        return templates.TemplateResponse(
            request,
            "library.html",
            _nav_ctx(request, books=books, plan=plan, notice=notice),
        )


@app.get("/book/{slug}", response_class=HTMLResponse)
async def book_page(request: Request, slug: str):
    with db() as conn:
        book = get_book(conn, slug)
        if not book:
            return RedirectResponse("/library", status_code=302)
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
        return templates.TemplateResponse(
            request,
            "book.html",
            _nav_ctx(request, book=book, chapters=views, state=state, plan=plan_row),
        )


@app.post("/book/{slug}/start")
async def start(slug: str):
    with db() as conn:
        book = get_book(conn, slug)
        reader = get_or_create_local_reader(conn)
        start_book(conn, reader["id"], book["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/book/{slug}/pause")
async def pause(slug: str):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        book = get_book(conn, slug)
        plan = conn.execute(
            "SELECT id FROM plans WHERE reader_id = ? AND book_id = ?",
            (reader["id"], book["id"]),
        ).fetchone()
        if plan:
            pause_plan(conn, plan["id"])
    return RedirectResponse(f"/book/{slug}", status_code=303)


@app.post("/book/{slug}/resume")
async def resume(slug: str):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        book = get_book(conn, slug)
        plan = conn.execute(
            "SELECT id FROM plans WHERE reader_id = ? AND book_id = ?",
            (reader["id"], book["id"]),
        ).fetchone()
        if plan:
            resume_plan(conn, plan["id"])
    return RedirectResponse(f"/book/{slug}", status_code=303)


@app.get("/read/{slug}/{position}", response_class=HTMLResponse)
async def read_chapter(request: Request, slug: str, position: int):
    with db() as conn:
        book = get_book(conn, slug)
        if not book:
            return RedirectResponse("/library", status_code=302)
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
            return RedirectResponse(f"/book/{slug}", status_code=302)
        chapter = get_chapter(conn, book["id"], position)
        nxt = get_chapter(conn, book["id"], position + 1)
        prev = get_chapter(conn, book["id"], position - 1) if position > 0 else None
        notes = reflections_for(conn, state.plan_id, position)
        return templates.TemplateResponse(
            request,
            "read.html",
            _nav_ctx(
                request,
                book=book,
                chapter=chapter,
                state=state,
                nxt=nxt if nxt and position + 1 <= state.unlocked_through else None,
                prev=prev,
                notes=notes,
            ),
        )


@app.post("/read/{slug}/{position}/note")
async def add_note(slug: str, position: int, body: str = Form(...)):
    with db() as conn:
        book = get_book(conn, slug)
        reader = get_or_create_local_reader(conn)
        plan_row = conn.execute(
            "SELECT id FROM plans WHERE reader_id = ? AND book_id = ?",
            (reader["id"], book["id"]),
        ).fetchone()
        if plan_row and body.strip():
            add_reflection(conn, plan_row["id"], position, body)
    return RedirectResponse(f"/read/{slug}/{position}", status_code=303)


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        return RedirectResponse("/library?notice=empty", status_code=303)
    path = save_upload(file.filename or "book.mobi", data)
    try:
        with db() as conn:
            book = ingest_file(path, conn)
        return RedirectResponse(f"/book/{book['slug']}", status_code=303)
    except Exception as exc:
        log.exception("Ingest failed")
        return RedirectResponse("/library?notice=fail", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        token = bot_token(conn)
        masked = _mask(token)
        return templates.TemplateResponse(
            request,
            "settings.html",
            _nav_ctx(
                request,
                reader=reader,
                token_masked=masked,
                has_token=bool(token),
                bot_running=telegram_app is not None,
            ),
        )


@app.post("/settings")
async def save_settings(
    timezone: str = Form(...),
    delivery_hhmm: str = Form(...),
    telegram_bot_token: str = Form(""),
):
    with db() as conn:
        reader = get_or_create_local_reader(conn)
        update_reader(conn, reader["id"], timezone=timezone.strip(), delivery_hhmm=delivery_hhmm.strip())
        if telegram_bot_token.strip():
            set_bot_token(conn, telegram_bot_token.strip())
    return RedirectResponse("/settings", status_code=303)


@app.get("/health")
async def health():
    return {"ok": True, "bot": telegram_app is not None}


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) < 10:
        return "••••"
    return token[:6] + "…" + token[-4:]


def create_app() -> FastAPI:
    return app
