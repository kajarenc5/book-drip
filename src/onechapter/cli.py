from __future__ import annotations

import argparse
import logging
import sys

from onechapter.config import HOST, PORT, ensure_dirs


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="onechapter", description="One chapter a day.")
    sub = parser.add_subparsers(dest="cmd")
    serve = sub.add_parser("serve", help="Run the reader and Telegram bot")
    serve.add_argument("--host", default=HOST)
    serve.add_argument("--port", type=int, default=PORT)
    ingest = sub.add_parser("ingest", help="Split a book file into daily chapters")
    ingest.add_argument("path")
    seed = sub.add_parser("seed-prince", help="Build The Prince from a Gutenberg text file")
    seed.add_argument("path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ensure_dirs()

    if args.cmd in (None, "serve"):
        host = getattr(args, "host", HOST)
        port = getattr(args, "port", PORT)
        import uvicorn

        uvicorn.run("onechapter.web.app:app", host=host, port=port, reload=False)
        return

    if args.cmd == "ingest":
        from pathlib import Path

        from onechapter.library import bootstrap, ingest_file

        bootstrap()
        book = ingest_file(Path(args.path))
        print(f"{book['title']}: {book['chapter_count']} chapters ({book['slug']})")
        return

    if args.cmd == "seed-prince":
        from pathlib import Path

        from onechapter.config import SEED_DIR
        from onechapter.seed import write_prince_seed

        dest = SEED_DIR / "the-prince.json"
        write_prince_seed(Path(args.path), dest)
        print(f"Wrote {dest}")
        return

    parser.print_help()
    sys.exit(1)
