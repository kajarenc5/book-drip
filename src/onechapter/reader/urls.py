from django.urls import path

from onechapter.reader import views

urlpatterns = [
    path("", views.today, name="today"),
    path("login", views.login_page, name="login"),
    path("logout", views.logout_view, name="logout"),
    path("auth/telegram", views.telegram_auth, name="telegram_auth"),
    path("auth/magic", views.magic_auth, name="magic_auth"),
    path("library", views.library, name="library"),
    path("book/<str:slug>", views.book_page, name="book"),
    path("book/<str:slug>/start", views.start, name="start"),
    path("book/<str:slug>/pause", views.pause, name="pause"),
    path("book/<str:slug>/resume", views.resume, name="resume"),
    path("read/<str:slug>/<int:position>", views.read_chapter, name="read"),
    path("read/<str:slug>/<int:position>/note", views.add_note, name="note"),
    path("upload", views.upload, name="upload"),
    path("settings", views.settings_page, name="settings"),
    path("health", views.health, name="health"),
]
