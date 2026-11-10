from onechapter.bot.runtime import runtime as telegram_runtime
from onechapter.config import env_bot_username, public_url


def shell(request):
    path = request.path
    snap = telegram_runtime.snapshot()
    return {
        "public_url": public_url(),
        "bot_running": snap["running"],
        "bot_username": snap["username"] or env_bot_username() or None,
        "nav_today": path == "/",
        "nav_library": path.startswith("/library") or path.startswith("/book"),
        "nav_settings": path.startswith("/settings"),
    }
