from __future__ import annotations

from django.contrib import admin

from onechapter import config
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


def is_admin_user_allowed(user) -> bool:
    """Restrict Django Admin strictly to active superusers matching operator identity rules."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not (user.is_active and user.is_staff and user.is_superuser):
        return False
    reader = Reader.objects.filter(user=user).first()
    if reader is not None and (reader.is_blocked or not reader.is_allowed or not reader.is_operator):
        return False
    op_ids = config.operator_telegram_ids()
    if op_ids:
        if reader is None:
            return False
        return ChannelIdentity.objects.filter(
            reader=reader,
            channel="telegram",
            external_id__in=op_ids,
        ).exists()
    if not config.is_local_single_user_dev() and config.access_mode() == "public":
        return False
    return True


admin.site.has_permission = lambda request: is_admin_user_allowed(getattr(request, "user", None))


@admin.register(Reader)
class ReaderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "timezone",
        "delivery_hhmm",
        "is_allowed",
        "is_blocked",
        "is_operator",
        "created_at",
    )
    list_filter = ("is_allowed", "is_blocked", "is_operator", "timezone")
    search_fields = ("display_name",)

    def save_model(self, request, obj, form, change):
        if change:
            if "is_blocked" in form.changed_data and obj.is_blocked:
                obj.is_allowed = False
            elif "is_allowed" in form.changed_data and not obj.is_allowed and "is_blocked" not in form.changed_data:
                obj.is_blocked = True
            elif "is_allowed" in form.changed_data and obj.is_allowed and "is_blocked" not in form.changed_data:
                obj.is_blocked = False
        super().save_model(request, obj, form, change)


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
