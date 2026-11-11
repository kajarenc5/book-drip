from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from onechapter.config import DEFAULT_TIMEZONE, MAX_UPLOAD_BYTES, ROOT, database_config

PACKAGE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = PACKAGE_DIR / "web"

DEFAULT_DEV_SECRET_KEY = "onechapter-local-reader-key-change-if-you-expose-this"
_INSECURE_SECRET_KEYS = {
    "",
    "change-me-in-production",
    DEFAULT_DEV_SECRET_KEY,
}

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", DEFAULT_DEV_SECRET_KEY)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") != "0"

if not DEBUG and "collectstatic" not in sys.argv:
    if SECRET_KEY.strip() in _INSECURE_SECRET_KEYS:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set to a strong, non-default secret when DJANGO_DEBUG=0."
        )

DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES

_allowed_hosts_env = os.environ.get("DJANGO_ALLOWED_HOSTS", "").strip()
if _allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(",") if h.strip()]
elif DEBUG:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = ["app.onechapter.am", "onechapter.am", "localhost", "127.0.0.1"]

_csrf_origins_env = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").strip()
if _csrf_origins_env:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins_env.split(",") if o.strip()]
else:
    CSRF_TRUSTED_ORIGINS = ["https://app.onechapter.am", "https://onechapter.am"]

APPEND_SLASH = False

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "onechapter.reader.apps.ReaderConfig",
]

_HAS_WHITENOISE = importlib.util.find_spec("whitenoise") is not None

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *(["whitenoise.middleware.WhiteNoiseMiddleware"] if _HAS_WHITENOISE else []),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "onechapter.reader.middleware.BootstrapMiddleware",
]

ROOT_URLCONF = "onechapter.project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [WEB_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "onechapter.reader.context_processors.shell",
            ],
            "builtins": [
                "onechapter.reader.templatetags.reader_extras",
            ],
        },
    }
]

DATABASES = {
    "default": database_config(),
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = DEFAULT_TIMEZONE
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [WEB_DIR / "static"]
STATIC_ROOT = ROOT / "staticfiles"

if _HAS_WHITENOISE and not DEBUG:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
