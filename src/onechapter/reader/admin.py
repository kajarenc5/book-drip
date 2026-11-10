from __future__ import annotations

from django.contrib import admin

from onechapter.reader.models import (
    AppSetting,
    Book,
    ChannelIdentity,
    Chapter,
    Delivery,
    Plan,
    Reader,
    Reflection,
)


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "timezone", "delivery_hhmm", "is_allowed", "is_operator", "created_at")
    list_filter = ("is_allowed", "is_operator", "timezone")
    search_fields = ("display_name",)


@admin.register(ChannelIdentity)
class ChannelIdentityAdmin(admin.ModelAdmin):
    list_display = ("id", "reader", "channel", "external_id", "handle", "is_delivery_enabled", "verified_at")
    list_filter = ("channel", "is_delivery_enabled")
    search_fields = ("external_id", "handle", "reader__display_name")


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("id", "slug", "title", "author", "profile", "uploaded_by", "source_filename", "created_at")
    list_filter = ("profile", "language")
    search_fields = ("slug", "title", "author", "source_filename", "source_sha256")
    readonly_fields = ("source_sha256", "s3_key", "created_at")


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ("id", "book", "position", "chapter_key", "title", "word_count")
    list_filter = ("book",)
    search_fields = ("title", "chapter_key", "body")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("id", "reader", "book", "status", "started_on", "paused_on")
    list_filter = ("status",)


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "plan", "local_date", "chapter_position", "channel", "delivered_at")
    list_filter = ("channel", "local_date")


@admin.register(Reflection)
class ReflectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reader",
        "book",
        "chapter_key",
        "chapter_position_snapshot",
        "kind",
        "source_channel",
        "created_at",
    )
    list_filter = ("kind", "source_channel", "book")
    search_fields = ("body", "transcript_text", "chapter_title_snapshot", "chapter_key")


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value")
