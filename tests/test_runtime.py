from onechapter.bot.runtime import TelegramRuntime


def test_runtime_starts_idle():
    rt = TelegramRuntime()
    assert rt.running is False
    snap = rt.snapshot()
    assert snap == {"running": False, "username": None, "bot_id": None}
