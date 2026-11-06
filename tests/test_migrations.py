import sqlite3

import pytest

from onechapter.db import SCHEMA, connect, init_db, migrate


def _version(conn) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _columns(conn, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def _reader(conn, name: str = "Ada") -> None:
    conn.execute(
        "INSERT INTO readers(name, timezone, delivery_hhmm) VALUES (?, ?, ?)",
        (name, "Asia/Yerevan", "08:00"),
    )
    conn.commit()


LOCALE = "ALTER TABLE readers ADD COLUMN locale TEXT NOT NULL DEFAULT 'en'"
LOCALE_SCHEMA = SCHEMA.replace(
    "delivery_hhmm TEXT NOT NULL,",
    "delivery_hhmm TEXT NOT NULL,\n    locale TEXT NOT NULL DEFAULT 'en',",
)


def test_new_database_is_created_at_baseline(data_dir):
    conn = connect()
    assert migrate(conn) == 1
    assert _version(conn) == 1
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "books",
        "chapters",
        "readers",
        "plans",
        "deliveries",
        "reflections",
        "settings",
    } <= tables
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_second_startup_keeps_rows(data_dir):
    conn = connect()
    migrate(conn)
    conn.execute("INSERT INTO settings(key, value) VALUES ('token', 'x')")
    conn.commit()
    assert migrate(conn) == 1
    assert conn.execute("SELECT value FROM settings WHERE key = 'token'").fetchone()["value"] == "x"


def test_init_db_stamps_a_legacy_file_without_rebuilding_it(data_dir):
    conn = connect()
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO settings(key, value) VALUES ('k', 'v')")
    conn.commit()
    conn.close()

    init_db()

    conn = connect()
    assert _version(conn) == 1
    assert conn.execute("SELECT value FROM settings WHERE key = 'k'").fetchone()["value"] == "v"
    init_db()
    assert _version(conn) == 1


def test_legacy_database_runs_pending_steps(data_dir):
    conn = connect()
    conn.executescript(SCHEMA)
    _reader(conn)
    assert _version(conn) == 0

    version = migrate(conn, migrations=((2, LOCALE),))

    assert version == 2
    row = conn.execute("SELECT name, locale FROM readers").fetchone()
    assert row["name"] == "Ada"
    assert row["locale"] == "en"
    assert migrate(conn, migrations=((2, LOCALE),)) == 2


def test_new_database_skips_migration_statements(data_dir):
    conn = connect()
    assert "locale TEXT" in LOCALE_SCHEMA
    # Running this statement would raise; a new database must not replay it.
    version = migrate(
        conn,
        schema=LOCALE_SCHEMA,
        migrations=((2, "SELECT this_migration_must_not_run FROM readers"),),
    )
    assert version == 2
    assert "locale" in _columns(conn, "readers")


def test_failed_migration_rolls_back_the_step_and_the_version(data_dir):
    conn = connect()
    migrate(conn)
    conn.execute("INSERT INTO settings(key, value) VALUES ('k', 'old')")
    conn.commit()

    with pytest.raises(sqlite3.OperationalError):
        migrate(
            conn,
            migrations=((2, "UPDATE settings SET value = 'new' WHERE key = 'k'; SELECT missing FROM settings"),),
        )

    assert _version(conn) == 1
    assert conn.execute("SELECT value FROM settings WHERE key = 'k'").fetchone()["value"] == "old"

    with pytest.raises(sqlite3.OperationalError):
        migrate(
            conn,
            migrations=((2, f"{LOCALE}; SELECT missing FROM readers"),),
        )
    assert _version(conn) == 1
    assert "locale" not in _columns(conn, "readers")


def test_failed_later_step_keeps_the_earlier_step(data_dir):
    conn = connect()
    migrate(conn)
    with pytest.raises(sqlite3.OperationalError):
        migrate(
            conn,
            migrations=(
                (2, LOCALE),
                (3, "ALTER TABLE readers ADD COLUMN locale TEXT"),
            ),
        )
    assert _version(conn) == 2
    assert "locale" in _columns(conn, "readers")

    assert migrate(
        conn,
        migrations=(
            (2, LOCALE),
            (3, "ALTER TABLE readers ADD COLUMN extra TEXT NOT NULL DEFAULT ''"),
        ),
    ) == 3
    assert "extra" in _columns(conn, "readers")


def test_migration_sql_keeps_semicolons_inside_strings_and_comments(data_dir):
    conn = connect()
    conn.executescript(SCHEMA)
    _reader(conn)
    migrate(
        conn,
        migrations=(
            (
                2,
                "-- do not split; this comment\n"
                "/* neither; this */\n"
                "UPDATE readers SET name = 'semi;colon' WHERE name = 'Ada'",
            ),
        ),
    )
    assert conn.execute("SELECT name FROM readers").fetchone()["name"] == "semi;colon"


def test_newer_database_is_refused(data_dir):
    conn = connect()
    migrate(conn)
    conn.execute("PRAGMA user_version = 9")
    conn.commit()
    with pytest.raises(RuntimeError, match="newer than this build"):
        migrate(conn)
    assert _version(conn) == 9


def test_migration_list_must_be_contiguous_and_free_of_version_control(data_dir):
    conn = connect()
    with pytest.raises(ValueError, match="expected 2"):
        migrate(conn, migrations=((3, "SELECT 1"),))
    assert _version(conn) == 0
    with pytest.raises(ValueError, match="user_version"):
        migrate(conn, migrations=((2, "PRAGMA user_version = 2"),))
    with pytest.raises(ValueError, match="transactions"):
        migrate(conn, migrations=((2, "BEGIN"),))
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'books'"
    ).fetchone()
