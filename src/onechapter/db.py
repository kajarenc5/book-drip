from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from onechapter import config
from onechapter.config import ensure_dirs

log = logging.getLogger(__name__)

# Version 1 is the baseline schema below. A database created before this
# mechanism existed already has these tables and user_version 0; startup
# stamps it as version 1 and does not rebuild it.
#
# A new database is created from SCHEMA and stamped at the latest version.
# Migration statements are not replayed on that empty file, so SCHEMA has to
# include every change that MIGRATIONS applies to older files.
#
# To add a column:
#   1. Add the column to SCHEMA.
#   2. Append the next version to MIGRATIONS, for example:
#        (2, "ALTER TABLE readers ADD COLUMN locale TEXT NOT NULL DEFAULT 'en'")
#   Versions must start at 2 and increase by 1. Each step runs once, in order,
#   inside its own transaction, together with the user_version bump.
BASELINE_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    language TEXT,
    profile TEXT,
    source_filename TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    part TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    UNIQUE (book_id, position)
);

CREATE TABLE IF NOT EXISTS readers (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER UNIQUE,
    name TEXT,
    timezone TEXT NOT NULL,
    delivery_hhmm TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    reader_id INTEGER NOT NULL REFERENCES readers(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    started_on TEXT,
    paused_on TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    UNIQUE (reader_id, book_id)
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    local_date TEXT NOT NULL,
    chapter_position INTEGER NOT NULL,
    channel TEXT NOT NULL,
    delivered_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (plan_id, local_date, channel)
);

CREATE TABLE IF NOT EXISTS reflections (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    chapter_position INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Ordered upgrades for databases that already exist. Empty until the next change.
MIGRATIONS: tuple[tuple[int, str], ...] = ()

_TX_CONTROL = re.compile(r"(?is)^\s*(begin|commit|end|rollback|savepoint|release)\b")
_USER_VERSION = re.compile(r"(?is)\bpragma\s+user_version\b")


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    db_path = path or config.DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def latest_version(
    baseline: int = BASELINE_VERSION,
    migrations: tuple[tuple[int, str], ...] | None = None,
) -> int:
    steps = MIGRATIONS if migrations is None else migrations
    _validate_migrations(baseline, steps)
    if not steps:
        return baseline
    return steps[-1][0]


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        migrate(conn)
    finally:
        if own:
            conn.close()


def migrate(
    conn: sqlite3.Connection,
    *,
    schema: str = SCHEMA,
    baseline: int = BASELINE_VERSION,
    migrations: tuple[tuple[int, str], ...] | None = None,
) -> int:
    """Bring a database up to the latest schema version.

    An empty database is created from ``schema`` and stamped at the latest
    version. An older database runs only the steps above its current
    ``PRAGMA user_version``. Version 0 with the baseline tables already
    present is a file from before migrations shipped; it is stamped at
    ``baseline`` and then follows the same path.
    """
    steps = MIGRATIONS if migrations is None else migrations
    _validate_migrations(baseline, steps)
    latest = baseline if not steps else steps[-1][0]
    _prepare(conn)
    current = _user_version(conn)
    if current > latest:
        raise RuntimeError(
            f"Database schema version {current} is newer than this build ({latest})."
        )
    if current == 0 and not _has_table(conn, "books"):
        _run_transaction(conn, _split_sql(schema), latest)
        log.info("Created database at schema version %s", latest)
        return latest
    if current == 0:
        _run_transaction(conn, [], baseline)
        log.info("Stamped existing database as schema version %s", baseline)
        current = baseline
    for version, sql in steps:
        if version <= current:
            continue
        _run_transaction(conn, _split_sql(sql), version)
        log.info("Applied schema migration %s", version)
        current = version
    return current


def _prepare(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")


def _user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _validate_migrations(baseline: int, migrations: tuple[tuple[int, str], ...]) -> None:
    if isinstance(baseline, bool) or not isinstance(baseline, int) or baseline < 1:
        raise ValueError("Baseline schema version must be an integer >= 1.")
    expected = baseline + 1
    for version, sql in migrations:
        if isinstance(version, bool) or not isinstance(version, int) or version != expected:
            raise ValueError(
                f"Migration versions must start at {baseline + 1} and increase by 1; "
                f"got {version!r}, expected {expected}."
            )
        if not isinstance(sql, str):
            raise ValueError(f"Migration {version} must be a SQL string.")
        statements = _split_sql(sql)
        if not statements:
            raise ValueError(f"Migration {version} has no SQL.")
        for statement in statements:
            if _TX_CONTROL.match(statement) or _USER_VERSION.search(statement):
                raise ValueError(
                    f"Migration {version} must not change transactions or user_version."
                )
        expected += 1


def _run_transaction(conn: sqlite3.Connection, statements: list[str], version: int) -> None:
    """Run statements and the version bump, or leave both unapplied."""
    previous = conn.isolation_level
    # The sqlite3 module commits implicitly before DDL unless autocommit is on.
    # An explicit transaction is what makes a failed ALTER roll back.
    conn.isolation_level = None
    began = False
    try:
        conn.execute("BEGIN")
        began = True
        for statement in statements:
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("COMMIT")
        began = False
    except Exception:
        if began:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = previous


def _split_sql(script: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    length = len(script)
    in_single = False
    in_double = False
    while i < length:
        char = script[i]
        nxt = script[i + 1] if i + 1 < length else ""
        if not in_single and not in_double and char == "-" and nxt == "-":
            i += 2
            while i < length and script[i] != "\n":
                i += 1
            continue
        if not in_single and not in_double and char == "/" and nxt == "*":
            end = script.find("*/", i + 2)
            i = length if end < 0 else end + 2
            continue
        if char == "'" and not in_double:
            buf.append(char)
            if in_single and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue
        if char == '"' and not in_single:
            buf.append(char)
            if in_double and nxt == '"':
                buf.append(nxt)
                i += 2
                continue
            in_double = not in_double
            i += 1
            continue
        if char == ";" and not in_single and not in_double:
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


@contextmanager
def db() -> sqlite3.Connection:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
