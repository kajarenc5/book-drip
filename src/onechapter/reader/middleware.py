import asyncio

from asgiref.sync import sync_to_async
from django.utils.decorators import sync_and_async_middleware

from onechapter.db import sync_sqlite_db_path
from onechapter.library import bootstrap

_ready: set[str] = set()


def _ensure_bootstrapped() -> None:
    key = sync_sqlite_db_path()
    if key in _ready:
        return
    bootstrap()
    _ready.add(key)


@sync_and_async_middleware
def BootstrapMiddleware(get_response):
    """Ensure migrations and built-in seed books exist before serving a request."""
    if asyncio.iscoroutinefunction(get_response):
        async def async_middleware(request):
            await sync_to_async(_ensure_bootstrapped)()
            return await get_response(request)

        return async_middleware

    def sync_middleware(request):
        _ensure_bootstrapped()
        return get_response(request)

    return sync_middleware
