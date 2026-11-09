from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from onechapter.config import DEFAULT_DELIVERY, DEFAULT_TIMEZONE


class DictAccessMixin:
    """Allow dict-style lookup (obj['field']) alongside attribute access."""

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def keys(self) -> list[str]:
        fields = [f.name for f in self._meta.concrete_fields]
        for f in self._meta.concrete_fields:
            if f.attname not in fields:
                fields.append(f.attname)
        for extra in (
            "chapter_count",
            "word_count",
            "name",
            "telegram_id",
            "timezone",
            "delivery_hhmm",
            "reader_name",
            "slug",
            "book_title",
            "author",
        ):
            if hasattr(self, extra) and extra not in fields:
                fields.append(extra)
        return fields


class Reader(DictAccessMixin, models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reader_profile",
        null=True,
        blank=True,
    )
    display_name = models.CharField(max_length=255, blank=True, default="")
    timezone = models.CharField(max_length=64, default=DEFAULT_TIMEZONE)
    delivery_hhmm = models.CharField(max_length=5, default=DEFAULT_DELIVERY)
    is_allowed = models.BooleanField(default=True)
    is_operator = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "readers"
        ordering = ["id"]

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def telegram_id(self) -> int | None:
        if hasattr(self, "_prefetched_telegram_id"):
            return self._prefetched_telegram_id
        ident = self.channels.filter(channel="telegram").first()
        if not ident:
            return None
        try:
            return int(ident.external_id)
        except (TypeError, ValueError):
            return None


class ChannelIdentity(DictAccessMixin, models.Model):
    CHANNEL_CHOICES = [
        ("telegram", "Telegram"),
        ("signal", "Signal"),
        ("whatsapp", "WhatsApp"),
        ("email", "Email"),
    ]

    reader = models.ForeignKey(
        Reader,
        on_delete=models.CASCADE,
        related_name="channels",
    )
    channel = models.CharField(max_length=32, choices=CHANNEL_CHOICES)
    external_id = models.CharField(max_length=255)
    handle = models.CharField(max_length=255, blank=True, default="")
    is_delivery_enabled = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    verified_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "channel_identities"
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_id"],
                name="uniq_channel_external_id",
            ),
        ]


class Book(DictAccessMixin, models.Model):
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=500)
    author = models.CharField(max_length=500, null=True, blank=True)
    language = models.CharField(max_length=16, null=True, blank=True)
    profile = models.CharField(max_length=64, null=True, blank=True)
    source_filename = models.CharField(max_length=500, null=True, blank=True)
    source_sha256 = models.CharField(max_length=64, unique=True, null=True, blank=True)
    s3_key = models.CharField(max_length=1024, null=True, blank=True)
    # Internal audit only — never rendered in templates, JSON responses, or bot replies.
    uploaded_by = models.ForeignKey(
        Reader,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_books",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "books"
        ordering = ["id"]


class Chapter(DictAccessMixin, models.Model):
    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="chapters",
    )
    position = models.PositiveIntegerField()
    chapter_key = models.CharField(max_length=255)
    content_sha256 = models.CharField(max_length=64, blank=True, default="")
    part = models.CharField(max_length=500, null=True, blank=True)
    title = models.CharField(max_length=500)
    body = models.TextField()
    word_count = models.PositiveIntegerField()

    class Meta:
        db_table = "chapters"
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["book", "position"],
                name="uniq_book_chapter_position",
            ),
            models.UniqueConstraint(
                fields=["book", "chapter_key"],
                name="uniq_book_chapter_key",
            ),
        ]


class Plan(DictAccessMixin, models.Model):
    reader = models.ForeignKey(
        Reader,
        on_delete=models.CASCADE,
        related_name="plans",
    )
    book = models.ForeignKey(
        Book,
        on_delete=models.CASCADE,
        related_name="plans",
    )
    started_on = models.DateField(null=True, blank=True)
    paused_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=32, default="idle")

    class Meta:
        db_table = "plans"
        constraints = [
            models.UniqueConstraint(
                fields=["reader", "book"],
                name="uniq_reader_book_plan",
            ),
        ]

    @property
    def timezone(self) -> str:
        return self.reader.timezone

    @property
    def delivery_hhmm(self) -> str:
        return self.reader.delivery_hhmm

    @property
    def telegram_id(self) -> int | None:
        return self.reader.telegram_id

    @property
    def reader_name(self) -> str:
        return self.reader.display_name

    @property
    def slug(self) -> str:
        return self.book.slug

    @property
    def book_title(self) -> str:
        return self.book.title

    @property
    def author(self) -> str | None:
        return self.book.author


class Delivery(DictAccessMixin, models.Model):
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    local_date = models.DateField()
    chapter_position = models.PositiveIntegerField()
    channel = models.CharField(max_length=32)
    delivered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "local_date", "channel"],
                name="uniq_plan_date_channel",
            ),
        ]


class Reflection(DictAccessMixin, models.Model):
    KIND_CHOICES = [
        ("text", "Text"),
        ("audio", "Audio"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reader = models.ForeignKey(
        Reader,
        on_delete=models.CASCADE,
        related_name="reflections",
    )
    book = models.ForeignKey(
        Book,
        on_delete=models.PROTECT,
        related_name="reflections",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reflections",
    )
    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reflections",
    )
    chapter_key = models.CharField(max_length=255, db_index=True)
    chapter_position_snapshot = models.PositiveIntegerField()
    chapter_title_snapshot = models.CharField(max_length=500)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default="text")
    source_channel = models.CharField(max_length=32, default="web")
    body = models.TextField(blank=True, default="")
    audio_s3_key = models.CharField(max_length=1024, null=True, blank=True)
    audio_mime_type = models.CharField(max_length=128, null=True, blank=True)
    audio_duration_sec = models.PositiveIntegerField(null=True, blank=True)
    external_media_id = models.CharField(max_length=255, null=True, blank=True)
    transcript_text = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reflections"
        ordering = ["created_at"]

    @property
    def chapter_position(self) -> int:
        if self.chapter_id and self.chapter is not None:
            return self.chapter.position
        return self.chapter_position_snapshot


class AppSetting(DictAccessMixin, models.Model):
    key = models.CharField(max_length=128, primary_key=True)
    value = models.TextField()

    class Meta:
        db_table = "settings"
