import asyncio

from asgiref.sync import sync_to_async
from django.http import HttpResponseForbidden
from django.utils.decorators import sync_and_async_middleware

from onechapter.db import current_db_target
from onechapter.library import bootstrap

_ready: set[str] = set()


def _ensure_bootstrapped() -> None:
    key = current_db_target()
    if key in _ready:
        return
    bootstrap()
    _ready.add(key)


def _is_non_operator_admin_attempt(request) -> bool:
    if not request.path.startswith("/admin"):
        return False
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    from onechapter.reader.admin import is_admin_user_allowed

    return not is_admin_user_allowed(user)


@sync_and_async_middleware
def BootstrapMiddleware(get_response):
    """Ensure migrations/seeds exist and block non-operator sessions from /admin/."""
    if asyncio.iscoroutinefunction(get_response):
        async def async_middleware(request):
            await sync_to_async(_ensure_bootstrapped)()
            if await sync_to_async(_is_non_operator_admin_attempt)(request):
                return HttpResponseForbidden("Operator access only.")
            return await get_response(request)

        return async_middleware

    def sync_middleware(request):
        _ensure_bootstrapped()
        if _is_non_operator_admin_attempt(request):
            return HttpResponseForbidden("Operator access only.")
        return get_response(request)

    return sync_middleware
