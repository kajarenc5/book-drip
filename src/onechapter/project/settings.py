from __future__ import annotations

import os
from pathlib import Path

from onechapter.config import DEFAULT_TIMEZONE

PACKAGE_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = PACKAGE_DIR / "web"

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "onechapter-local-reader-key-change-if-you-expose-this",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") != "0"
ALLOWED_HOSTS = ["*"]
APPEND_SLASH = False

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "onechapter.reader.apps.ReaderConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
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
                "onechapter.reader.context_processors.shell",
            ],
            "builtins": [
                "onechapter.reader.templatetags.reader_extras",
            ],
        },
    }
]

# Books, plans, and deliveries live in the versioned SQLite file owned by
# onechapter.db. Django has no models of its own; this connection exists so
# the framework checks have a default database.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = DEFAULT_TIMEZONE
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [WEB_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
