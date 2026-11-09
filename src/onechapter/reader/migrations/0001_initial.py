from __future__ import annotations

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import onechapter.reader.models
from onechapter.config import DEFAULT_DELIVERY, DEFAULT_TIMEZONE


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSetting",
            fields=[
                ("key", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("value", models.TextField()),
            ],
            options={
                "db_table": "settings",
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Reader",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("display_name", models.CharField(blank=True, default="", max_length=255)),
                ("timezone", models.CharField(default=DEFAULT_TIMEZONE, max_length=64)),
                ("delivery_hhmm", models.CharField(default=DEFAULT_DELIVERY, max_length=5)),
                ("is_allowed", models.BooleanField(default=True)),
                ("is_operator", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reader_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "readers",
                "ordering": ["id"],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Book",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("slug", models.SlugField(max_length=255, unique=True)),
                ("title", models.CharField(max_length=500)),
                ("author", models.CharField(blank=True, max_length=500, null=True)),
                ("language", models.CharField(blank=True, max_length=16, null=True)),
                ("profile", models.CharField(blank=True, max_length=64, null=True)),
                ("source_filename", models.CharField(blank=True, max_length=500, null=True)),
                (
                    "source_sha256",
                    models.CharField(blank=True, max_length=64, null=True, unique=True),
                ),
                ("s3_key", models.CharField(blank=True, max_length=1024, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_books",
                        to="reader.reader",
                    ),
                ),
            ],
            options={
                "db_table": "books",
                "ordering": ["id"],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="ChannelIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("telegram", "Telegram"),
                            ("signal", "Signal"),
                            ("whatsapp", "WhatsApp"),
                            ("email", "Email"),
                        ],
                        max_length=32,
                    ),
                ),
                ("external_id", models.CharField(max_length=255)),
                ("handle", models.CharField(blank=True, default="", max_length=255)),
                ("is_delivery_enabled", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("verified_at", models.DateTimeField(auto_now_add=True)),
                (
                    "reader",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="channels",
                        to="reader.reader",
                    ),
                ),
            ],
            options={
                "db_table": "channel_identities",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("channel", "external_id"),
                        name="uniq_channel_external_id",
                    ),
                ],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Chapter",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("position", models.PositiveIntegerField()),
                ("chapter_key", models.CharField(max_length=255)),
                ("content_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("part", models.CharField(blank=True, max_length=500, null=True)),
                ("title", models.CharField(max_length=500)),
                ("body", models.TextField()),
                ("word_count", models.PositiveIntegerField()),
                (
                    "book",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chapters",
                        to="reader.book",
                    ),
                ),
            ],
            options={
                "db_table": "chapters",
                "ordering": ["position"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("book", "position"),
                        name="uniq_book_chapter_position",
                    ),
                    models.UniqueConstraint(
                        fields=("book", "chapter_key"),
                        name="uniq_book_chapter_key",
                    ),
                ],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Plan",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("started_on", models.DateField(blank=True, null=True)),
                ("paused_on", models.DateField(blank=True, null=True)),
                ("status", models.CharField(default="idle", max_length=32)),
                (
                    "book",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plans",
                        to="reader.book",
                    ),
                ),
                (
                    "reader",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="plans",
                        to="reader.reader",
                    ),
                ),
            ],
            options={
                "db_table": "plans",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("reader", "book"),
                        name="uniq_reader_book_plan",
                    ),
                ],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Delivery",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("local_date", models.DateField()),
                ("chapter_position", models.PositiveIntegerField()),
                ("channel", models.CharField(max_length=32)),
                ("delivered_at", models.DateTimeField(auto_now_add=True)),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="reader.plan",
                    ),
                ),
            ],
            options={
                "db_table": "deliveries",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("plan", "local_date", "channel"),
                        name="uniq_plan_date_channel",
                    ),
                ],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
        migrations.CreateModel(
            name="Reflection",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("chapter_key", models.CharField(db_index=True, max_length=255)),
                ("chapter_position_snapshot", models.PositiveIntegerField()),
                ("chapter_title_snapshot", models.CharField(max_length=500)),
                (
                    "kind",
                    models.CharField(
                        choices=[("text", "Text"), ("audio", "Audio")],
                        default="text",
                        max_length=16,
                    ),
                ),
                ("source_channel", models.CharField(default="web", max_length=32)),
                ("body", models.TextField(blank=True, default="")),
                ("audio_s3_key", models.CharField(blank=True, max_length=1024, null=True)),
                ("audio_mime_type", models.CharField(blank=True, max_length=128, null=True)),
                ("audio_duration_sec", models.PositiveIntegerField(blank=True, null=True)),
                ("external_media_id", models.CharField(blank=True, max_length=255, null=True)),
                ("transcript_text", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "book",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reflections",
                        to="reader.book",
                    ),
                ),
                (
                    "chapter",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reflections",
                        to="reader.chapter",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reflections",
                        to="reader.plan",
                    ),
                ),
                (
                    "reader",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reflections",
                        to="reader.reader",
                    ),
                ),
            ],
            options={
                "db_table": "reflections",
                "ordering": ["created_at"],
            },
            bases=(onechapter.reader.models.DictAccessMixin, models.Model),
        ),
    ]
