from __future__ import annotations

import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "onechapter.project.settings")

from django.core.asgi import get_asgi_application

log = logging.getLogger(__name__)
django_application = get_asgi_application()


async def _startup() -> None:
    from onechapter.bot.runtime import runtime as telegram_runtime
    from onechapter.db import db
    from onechapter.library import bootstrap, bot_token

    bootstrap()
    token = ""
    with db() as conn:
        token = bot_token(conn)
    if token:
        try:
            await telegram_runtime.start(token)
        except ValueError as exc:
            log.warning("Telegram bot did not start: %s", exc)
    else:
        log.info("No TELEGRAM_BOT_TOKEN — web reader only; paste one on /settings to start")


async def _shutdown() -> None:
    from onechapter.bot.runtime import runtime as telegram_runtime

    await telegram_runtime.stop()


async def _lifespan(receive, send) -> None:
    started = False
    while True:
        message = await receive()
        kind = message["type"]
        if kind == "lifespan.startup":
            try:
                await _startup()
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
            started = True
            await send({"type": "lifespan.startup.complete"})
        elif kind == "lifespan.shutdown":
            if started:
                await _shutdown()
            await send({"type": "lifespan.shutdown.complete"})
            return


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        await _lifespan(receive, send)
        return
    await django_application(scope, receive, send)
