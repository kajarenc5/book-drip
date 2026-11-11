from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("onechapter.reader.urls")),
]

if settings.DEBUG:
    urlpatterns.append(
        re_path(
            r"^static/(?P<path>.*)$",
            serve,
            {"document_root": str(settings.STATICFILES_DIRS[0])},
        )
    )
