from django.utils.deprecation import MiddlewareMixin

from onechapter import config
from onechapter.library import bootstrap

_ready: set[str] = set()


class BootstrapMiddleware(MiddlewareMixin):
    """Create the reader database and built-in books on first use of a path."""

    def process_request(self, request):
        key = str(config.DB_PATH)
        if key in _ready:
            return None
        bootstrap()
        _ready.add(key)
        return None
