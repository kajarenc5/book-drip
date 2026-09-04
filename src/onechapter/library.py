from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from onechapter import config
from onechapter.config import DEFAULT_DELIVERY, DEFAULT_TIMEZONE, ensure_dirs
from onechapter.db import db, init_db
from onechapter.ingest.extract import extract_path
from onechapter.ingest.split import SplitResult, split_extracted
from onechapter.ingest.textutil import slugify
from onechapter.reading import ChapterView, PlanState, plan_state, today_in


def bootstrap() -> None:
    ensure_dirs()
    init_db()
    from onechapter.seed import seed_builtin_books

    seed_builtin_books()


def list_books(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT b.*, COUNT(c.id) AS chapter_count, SUM(c.word_count) AS word_count
        FROM books b
        LEFT JOIN chapters c ON c.book_id = b.id
        GROUP BY b.id
        ORDER BY b.id
        """
    ).fetchall()


def get_book(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT b.*, COUNT(c.id) AS chapter_count
        FROM books b
        LEFT JOIN chapters c ON c.book_id = b.id
        WHERE b.slug = ?
        GROUP BY b.id
        """,
        (slug,),
    ).fetchone()


def get_book_by_id(conn: sqlite3.Connection, book_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def list_chapters(conn: sqlite3.Connection, book_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chapters WHERE book_id = ? ORDER BY position",
        (book_id,),
    ).fetchall()


def get_chapter(conn: sqlite3.Connection, book_id: int, position: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM chapters WHERE book_id = ? AND position = ?",
        (book_id, position),
    ).fetchone()


def upsert_book_from_split(
    conn: sqlite3.Connection,
    split: SplitResult,
    *,
    slug: str | None = None,
    filename: str | None = None,
    language: str | None = None,
) -> sqlite3.Row:
    slug = slug or slugify(split.title)
    base = slug
    n = 2
    while True:
        existing = conn.execute("SELECT id FROM books WHERE slug = ?", (slug,)).fetchone()
        if not existing:
            break
        # Replace if this is a re-ingest of the same source.
        if filename:
            same = conn.execute(
                "SELECT id FROM books WHERE slug = ? AND source_filename = ?",
                (slug, filename),
            ).fetchone()
            if same:
                _replace_book(conn, same["id"], split, filename)
                return get_book(conn, slug)
        slug = f"{base}-{n}"
        n += 1

    cur = conn.execute(
        "INSERT INTO books(slug, title, author, language, profile, source_filename) VALUES (?, ?, ?, ?, ?, ?)",
        (slug, split.title, split.author, language, split.profile, filename),
    )
    book_id = cur.lastrowid
    _insert_chapters(conn, book_id, split)
    return get_book(conn, slug)


def _replace_book(conn: sqlite3.Connection, book_id: int, split: SplitResult, filename: str | None) -> None:
    conn.execute(
        "UPDATE books SET title = ?, author = ?, profile = ?, source_filename = ? WHERE id = ?",
        (split.title, split.author, split.profile, filename, book_id),
    )
    conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
    _insert_chapters(conn, book_id, split)


def _insert_chapters(conn: sqlite3.Connection, book_id: int, split: SplitResult) -> None:
    conn.executemany(
        "INSERT INTO chapters(book_id, position, part, title, body, word_count) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (book_id, ch.index, ch.part, ch.title, ch.body, ch.words)
            for ch in split.chapters
        ],
    )


def ingest_file(path: Path, conn: sqlite3.Connection | None = None) -> sqlite3.Row:
    extracted = extract_path(path)
    split = split_extracted(extracted)
    if not split.chapters:
        raise ValueError("No chapters found in that file.")
    language = "ru" if split.profile == "tarasov" else None

    def _save(c: sqlite3.Connection) -> sqlite3.Row:
        slug = None
        if split.profile == "tarasov":
            slug = "tehnologiya-zhizni"
        return upsert_book_from_split(
            c, split, slug=slug, filename=path.name, language=language
        )

    if conn is not None:
        return _save(conn)
    with db() as c:
        return _save(c)


def save_upload(filename: str, data: bytes) -> Path:
    ensure_dirs()
    safe = Path(filename).name
    dest = config.UPLOAD_DIR / safe
    dest.write_bytes(data)
    return dest


def get_or_create_local_reader(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM readers WHERE telegram_id IS NULL ORDER BY id LIMIT 1").fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO readers(telegram_id, name, timezone, delivery_hhmm) VALUES (NULL, ?, ?, ?)",
        ("Local reader", DEFAULT_TIMEZONE, DEFAULT_DELIVERY),
    )
    return conn.execute("SELECT * FROM readers WHERE telegram_id IS NULL ORDER BY id LIMIT 1").fetchone()


def get_or_create_telegram_reader(
    conn: sqlite3.Connection, telegram_id: int, name: str | None
) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM readers WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row:
        if name and row["name"] != name:
            conn.execute("UPDATE readers SET name = ? WHERE id = ?", (name, row["id"]))
            row = conn.execute("SELECT * FROM readers WHERE id = ?", (row["id"],)).fetchone()
        return row
    conn.execute(
        "INSERT INTO readers(telegram_id, name, timezone, delivery_hhmm) VALUES (?, ?, ?, ?)",
        (telegram_id, name, DEFAULT_TIMEZONE, DEFAULT_DELIVERY),
    )
    return conn.execute("SELECT * FROM readers WHERE telegram_id = ?", (telegram_id,)).fetchone()


def update_reader(
    conn: sqlite3.Connection,
    reader_id: int,
    *,
    timezone: str | None = None,
    delivery_hhmm: str | None = None,
    name: str | None = None,
) -> None:
    if timezone:
        conn.execute("UPDATE readers SET timezone = ? WHERE id = ?", (timezone, reader_id))
    if delivery_hhmm:
        conn.execute("UPDATE readers SET delivery_hhmm = ? WHERE id = ?", (delivery_hhmm, reader_id))
    if name is not None:
        conn.execute("UPDATE readers SET name = ? WHERE id = ?", (name, reader_id))


def get_plan(conn: sqlite3.Connection, reader_id: int, book_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.*, r.timezone, r.delivery_hhmm, r.telegram_id, r.name AS reader_name
        FROM plans p
        JOIN readers r ON r.id = p.reader_id
        WHERE p.reader_id = ? AND p.book_id = ?
        """,
        (reader_id, book_id),
    ).fetchone()


def active_plan_for_reader(conn: sqlite3.Connection, reader_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.*, r.timezone, r.delivery_hhmm, r.telegram_id, r.name AS reader_name,
               b.slug, b.title AS book_title, b.author
        FROM plans p
        JOIN readers r ON r.id = p.reader_id
        JOIN books b ON b.id = p.book_id
        WHERE p.reader_id = ? AND p.status IN ('active', 'paused')
        ORDER BY CASE p.status WHEN 'active' THEN 0 ELSE 1 END, p.id DESC
        LIMIT 1
        """,
        (reader_id,),
    ).fetchone()


def list_active_plans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*, r.timezone, r.delivery_hhmm, r.telegram_id, r.name AS reader_name,
               b.slug, b.title AS book_title, b.author
        FROM plans p
        JOIN readers r ON r.id = p.reader_id
        JOIN books b ON b.id = p.book_id
        WHERE p.status = 'active' AND r.telegram_id IS NOT NULL
        """
    ).fetchall()


def chapter_count(conn: sqlite3.Connection, book_id: int) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM chapters WHERE book_id = ?", (book_id,)).fetchone()
    return int(row["n"])


def state_for_plan(conn: sqlite3.Connection, plan_row: sqlite3.Row, now: datetime | None = None) -> PlanState:
    total = chapter_count(conn, plan_row["book_id"])
    return plan_state(plan_row, total, now)


def start_book(conn: sqlite3.Connection, reader_id: int, book_id: int, now: datetime | None = None) -> PlanState:
    reader = conn.execute("SELECT * FROM readers WHERE id = ?", (reader_id,)).fetchone()
    today = today_in(reader["timezone"], now)
    other = conn.execute(
        "SELECT id FROM plans WHERE reader_id = ? AND status IN ('active', 'paused') AND book_id != ?",
        (reader_id, book_id),
    ).fetchall()
    for row in other:
        conn.execute("UPDATE plans SET status = 'idle', paused_on = NULL WHERE id = ?", (row["id"],))
    existing = conn.execute(
        "SELECT * FROM plans WHERE reader_id = ? AND book_id = ?",
        (reader_id, book_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE plans SET status = 'active', started_on = ?, paused_on = NULL WHERE id = ?",
            (today.isoformat(), existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO plans(reader_id, book_id, started_on, status) VALUES (?, ?, ?, 'active')",
            (reader_id, book_id, today.isoformat()),
        )
    plan = get_plan(conn, reader_id, book_id)
    return state_for_plan(conn, plan, now)


def pause_plan(conn: sqlite3.Connection, plan_id: int, now: datetime | None = None) -> None:
    row = conn.execute(
        """
        SELECT p.*, r.timezone FROM plans p JOIN readers r ON r.id = p.reader_id WHERE p.id = ?
        """,
        (plan_id,),
    ).fetchone()
    if not row or row["status"] != "active":
        return
    today = today_in(row["timezone"], now)
    conn.execute(
        "UPDATE plans SET status = 'paused', paused_on = ? WHERE id = ?",
        (today.isoformat(), plan_id),
    )


def resume_plan(conn: sqlite3.Connection, plan_id: int, now: datetime | None = None) -> None:
    row = conn.execute(
        """
        SELECT p.*, r.timezone FROM plans p JOIN readers r ON r.id = p.reader_id WHERE p.id = ?
        """,
        (plan_id,),
    ).fetchone()
    if not row or row["status"] != "paused" or not row["started_on"] or not row["paused_on"]:
        return
    today = today_in(row["timezone"], now)
    from datetime import date

    started = date.fromisoformat(row["started_on"])
    paused = date.fromisoformat(row["paused_on"])
    shift = (today - paused).days
    new_start = started.fromordinal(started.toordinal() + shift)
    conn.execute(
        "UPDATE plans SET status = 'active', started_on = ?, paused_on = NULL WHERE id = ?",
        (new_start.isoformat(), plan_id),
    )


def chapter_views(conn: sqlite3.Connection, book_id: int, state: PlanState | None) -> list[ChapterView]:
    unlocked = state.unlocked_through if state and state.status in {"active", "paused", "finished"} else -1
    if state and state.finished:
        unlocked = state.total - 1
    views = []
    for row in list_chapters(conn, book_id):
        views.append(
            ChapterView(
                position=row["position"],
                title=row["title"],
                part=row["part"],
                body=row["body"],
                word_count=row["word_count"],
                unlocked=row["position"] <= unlocked,
            )
        )
    return views


def record_delivery(
    conn: sqlite3.Connection, plan_id: int, local_date: str, position: int, channel: str
) -> bool:
    try:
        conn.execute(
            "INSERT INTO deliveries(plan_id, local_date, chapter_position, channel) VALUES (?, ?, ?, ?)",
            (plan_id, local_date, position, channel),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def already_delivered(conn: sqlite3.Connection, plan_id: int, local_date: str, channel: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM deliveries WHERE plan_id = ? AND local_date = ? AND channel = ?",
        (plan_id, local_date, channel),
    ).fetchone()
    return bool(row)


def add_reflection(conn: sqlite3.Connection, plan_id: int, position: int, body: str) -> None:
    conn.execute(
        "INSERT INTO reflections(plan_id, chapter_position, body) VALUES (?, ?, ?)",
        (plan_id, position, body.strip()),
    )


def reflections_for(conn: sqlite3.Connection, plan_id: int, position: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reflections WHERE plan_id = ? AND chapter_position = ? ORDER BY id",
        (plan_id, position),
    ).fetchall()


def bot_token(conn: sqlite3.Connection) -> str:
    from onechapter.config import env_bot_token
    from onechapter.db import setting

    return setting(conn, "telegram_bot_token", "") or env_bot_token()


def set_bot_token(conn: sqlite3.Connection, token: str) -> None:
    from onechapter.db import set_setting

    set_setting(conn, "telegram_bot_token", token.strip())


def estimate_days(word_count_value: int) -> str:
    return f"{word_count_value} words"
