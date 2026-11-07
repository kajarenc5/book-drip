from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("", include("onechapter.reader.urls")),
    re_path(
        r"^static/(?P<path>.*)$",
        serve,
        {"document_root": str(settings.STATICFILES_DIRS[0])},
    ),
]
