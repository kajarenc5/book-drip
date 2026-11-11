from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from onechapter.config import HOST, PORT, ensure_dirs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="onechapter", description="One chapter a day.")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="Run the reader and Telegram bot")
    serve.add_argument("--host", default=HOST)
    serve.add_argument("--port", type=int, default=PORT)
    serve.add_argument(
        "--no-bot",
        action="store_true",
        help="Run the web reader only without starting Telegram bot polling",
    )
    ingest = sub.add_parser("ingest", help="Split a book file into daily chapters")
    ingest.add_argument("path")
    reprocess = sub.add_parser(
        "reprocess",
        help="Operator command: re-fetch stored original from S3/uploads and re-split in place",
    )
    reprocess.add_argument("slug")
    db_cmd = sub.add_parser("db", help="Manage the local PostgreSQL 17 instance")
    db_cmd.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["start", "stop", "status", "psql", "reset"],
        help="Local PostgreSQL action (default: status)",
    )
    seed = sub.add_parser("seed-prince", help="Build The Prince from a Gutenberg text file")
    seed.add_argument("path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onechapter.project.settings")
    ensure_dirs()

    if args.cmd == "db":
        _handle_db_command(args.action)
        return

    if args.cmd in (None, "serve"):
        if getattr(args, "no_bot", False):
            os.environ["ONECHAPTER_DISABLE_BOT_POLLING"] = "1"
        host = getattr(args, "host", HOST)
        port = getattr(args, "port", PORT)
        import uvicorn

        uvicorn.run("onechapter.project.asgi:application", host=host, port=port, reload=False)
        return

    if args.cmd == "ingest":
        import django

        django.setup()
        from onechapter.library import bootstrap, ingest_file

        bootstrap()
        book = ingest_file(Path(args.path))
        print(f"{book['title']}: {book['chapter_count']} chapters ({book['slug']})")
        return

    if args.cmd == "reprocess":
        import django

        django.setup()
        from onechapter.library import bootstrap, reprocess_book

        bootstrap()
        book = reprocess_book(args.slug)
        print(f"Reprocessed {book['title']}: {book['chapter_count']} chapters ({book['slug']})")
        return

    if args.cmd == "seed-prince":
        from onechapter.config import SEED_DIR
        from onechapter.seed import write_prince_seed

        dest = SEED_DIR / "the-prince.json"
        write_prince_seed(Path(args.path), dest)
        print(f"Wrote {dest}")
        return

    parser.print_help()
    sys.exit(1)


def _handle_db_command(action: str) -> None:
    from onechapter.local_pg import (
        ensure_local_postgres,
        is_local_pg_running,
        local_database_url,
        local_pg_db,
        local_pg_dir,
        local_pg_host,
        local_pg_port,
        local_pg_user,
        pg_bin,
        reset_local_database,
        stop_local_postgres,
    )

    if action == "status":
        running = is_local_pg_running()
        state = "running" if running else "stopped"
        print(f"PostgreSQL 17 ({state})")
        print(f"  data dir: {local_pg_dir()}")
        print(f"  url:      {local_database_url()}")
        return

    if action == "start":
        info = ensure_local_postgres()
        import django

        django.setup()
        from onechapter.library import bootstrap

        bootstrap()
        print(f"PostgreSQL 17 running at {info['url']} (data: {info['pgdata']})")
        return

    if action == "stop":
        stopped = stop_local_postgres()
        print("PostgreSQL 17 stopped." if stopped else "PostgreSQL 17 was not running.")
        return

    if action == "reset":
        info = reset_local_database()
        import django

        django.setup()
        from onechapter.db import _migrated_targets
        from onechapter.library import bootstrap

        _migrated_targets.clear()
        bootstrap()
        print(f"Reset and re-seeded {info['url']}")
        return

    if action == "psql":
        info = ensure_local_postgres()
        os.execv(
            pg_bin("psql"),
            [
                "psql",
                "-h",
                local_pg_host(),
                "-p",
                str(local_pg_port()),
                "-U",
                local_pg_user(),
                "-d",
                local_pg_db(),
            ],
        )
