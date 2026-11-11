from __future__ import annotations

import logging
import os
import signal
import shutil
import subprocess
from pathlib import Path

from onechapter import config

log = logging.getLogger(__name__)

_PG_SEARCH_DIRS = (
    Path("/opt/homebrew/opt/postgresql@17/bin"),
    Path("/usr/local/opt/postgresql@17/bin"),
    Path("/usr/lib/postgresql/17/bin"),
)
_verified_local_dbs: set[str] = set()


def pg_bin(name: str) -> str:
    """Locate a PostgreSQL 17 client/server binary (initdb, pg_ctl, pg_isready, psql, createdb)."""
    for directory in _PG_SEARCH_DIRS:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(
        f"PostgreSQL binary '{name}' not found. Install PostgreSQL 17 with: brew install postgresql@17"
    )


def local_pg_dir() -> Path:
    explicit = os.environ.get("ONECHAPTER_PGDATA", "").strip()
    if explicit:
        return Path(explicit).resolve()
    return (config.ROOT / "data" / "postgres").resolve()


def local_pg_host() -> str:
    return os.environ.get("POSTGRES_HOST", "127.0.0.1").strip() or "127.0.0.1"


def local_pg_port() -> int:
    return int(os.environ.get("ONECHAPTER_PG_PORT", os.environ.get("POSTGRES_PORT", "54329")))


def local_pg_user() -> str:
    return os.environ.get("POSTGRES_USER", "onechapter").strip() or "onechapter"


def local_pg_password() -> str:
    return os.environ.get("POSTGRES_PASSWORD", "")


def local_pg_db() -> str:
    return os.environ.get("POSTGRES_DB", "onechapter").strip() or "onechapter"


def local_database_url(db_name: str | None = None) -> str:
    target_db = db_name or local_pg_db()
    user = local_pg_user()
    host = local_pg_host()
    port = local_pg_port()
    return f"postgresql://{user}@{host}:{port}/{target_db}"


def is_using_managed_local_pg() -> bool:
    """Return True when DATABASE_URL is unset and One Chapter manages the local Postgres 17 instance."""
    return not os.environ.get("DATABASE_URL", "").strip()


def _pg_env() -> dict[str, str]:
    """Return environment with LC_ALL=C so macOS Sequoia does not spawn a locale GCD thread in postmaster."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def is_local_pg_running() -> bool:
    host = local_pg_host()
    port = str(local_pg_port())
    try:
        res = subprocess.run(
            [pg_bin("pg_isready"), "-h", host, "-p", port, "-q"],
            check=False,
            capture_output=True,
            env=_pg_env(),
        )
        return res.returncode == 0
    except Exception:
        return False


def _cleanup_stale_postmaster(pgdata: Path) -> None:
    pid_file = pgdata / "postmaster.pid"
    if not pid_file.exists():
        return
    try:
        first_line = pid_file.read_text(encoding="utf-8").splitlines()[0].strip()
        pid = int(first_line)
        os.kill(pid, 0)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
    except (ValueError, IndexError, OSError):
        pass


def ensure_local_postgres(db_name: str | None = None, *, force: bool = False) -> dict[str, str]:
    """Ensure the local PostgreSQL 17 instance is initialized, running, and has the target database."""
    pgdata = local_pg_dir()
    host = local_pg_host()
    port = local_pg_port()
    user = local_pg_user()
    target_db = db_name or local_pg_db()
    cache_key = f"{pgdata}:{host}:{port}:{target_db}"

    if not force and cache_key in _verified_local_dbs:
        return {
            "pgdata": str(pgdata),
            "host": host,
            "port": str(port),
            "user": user,
            "dbname": target_db,
            "url": local_database_url(target_db),
        }

    env = _pg_env()

    if not is_local_pg_running():
        pgdata.parent.mkdir(parents=True, exist_ok=True)
        if not (pgdata / "PG_VERSION").exists():
            log.info("Initializing local PostgreSQL 17 instance in %s", pgdata)
            subprocess.run(
                [
                    pg_bin("initdb"),
                    "-D",
                    str(pgdata),
                    "-U",
                    user,
                    "--auth=trust",
                    "--encoding=UTF8",
                    "--no-locale",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        _cleanup_stale_postmaster(pgdata)
        logfile = pgdata / "postgres.log"
        opts = f"-p {port} -k /tmp -h {host}"
        subprocess.run(
            [pg_bin("pg_ctl"), "-D", str(pgdata), "-l", str(logfile), "-w", "-o", opts, "start"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    _ensure_database_exists(host=host, port=port, user=user, db_name=target_db)
    _verified_local_dbs.add(cache_key)
    return {
        "pgdata": str(pgdata),
        "host": host,
        "port": str(port),
        "user": user,
        "dbname": target_db,
        "url": local_database_url(target_db),
    }


def _ensure_database_exists(*, host: str, port: int, user: str, db_name: str) -> None:
    env = _pg_env()
    check_sql = f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'"
    res = subprocess.run(
        [
            pg_bin("psql"),
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            "postgres",
            "-tAc",
            check_sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    if res.stdout.strip() != "1":
        subprocess.run(
            [
                pg_bin("createdb"),
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                db_name,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )


def stop_local_postgres() -> bool:
    _verified_local_dbs.clear()
    pgdata = local_pg_dir()
    if not (pgdata / "PG_VERSION").exists():
        return False
    res = subprocess.run(
        [pg_bin("pg_ctl"), "-D", str(pgdata), "-m", "fast", "stop"],
        check=False,
        capture_output=True,
        text=True,
        env=_pg_env(),
    )
    if res.returncode != 0:
        pid_file = pgdata / "postmaster.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0].strip())
                os.kill(pid, signal.SIGTERM)
                return True
            except Exception:
                pass
        return False
    return True


def reset_local_database(db_name: str | None = None) -> dict[str, str]:
    info = ensure_local_postgres(db_name)
    target_db = info["dbname"]
    subprocess.run(
        [
            pg_bin("psql"),
            "-h",
            info["host"],
            "-p",
            info["port"],
            "-U",
            info["user"],
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'DROP DATABASE IF EXISTS "{target_db}" WITH (FORCE);',
            "-c",
            f'CREATE DATABASE "{target_db}";',
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_pg_env(),
    )
    return info
