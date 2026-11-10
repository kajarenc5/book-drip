from __future__ import annotations

import shutil
import uuid
from datetime import date, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce

from onechapter import config, storage
from onechapter.config import DEFAULT_DELIVERY, DEFAULT_TIMEZONE, ensure_dirs
from onechapter.db import init_db
from onechapter.ingest.extract import extract_path
from onechapter.ingest.split import ChapterDraft, SplitResult, split_extracted
from onechapter.ingest.textutil import slugify
from onechapter.reading import (
    ChapterView,
    PlanState,
    is_valid_timezone,
    normalize_hhmm,
    plan_state,
    today_in,
)


def _is_conn(obj) -> bool:
    if obj is None:
        return True
    return hasattr(obj, "cursor") or hasattr(obj, "execute")


def bootstrap() -> None:
    ensure_dirs()
    init_db()
    from onechapter.seed import seed_builtin_books

    seed_builtin_books()


def compute_chapter_keys(chapters: list[ChapterDraft]) -> list[tuple[ChapterDraft, str, str]]:
    """Compute deterministic (draft, chapter_key, content_sha256) tuples for a split book."""
    seen: dict[str, int] = {}
    out: list[tuple[ChapterDraft, str, str]] = []
    for ch in chapters:
        base_key = slugify(ch.title)
        if not base_key or base_key == "book":
            base_key = f"pos-{ch.index}"
        count = seen.get(base_key, 0) + 1
        seen[base_key] = count
        key = base_key if count == 1 else f"{base_key}-{count}"
        content_hash = storage.sha256_bytes(ch.body.strip().encode("utf-8"))
        out.append((ch, key, content_hash))
    return out


def list_books(conn=None):
    from onechapter.reader.models import Book

    init_db()
    return list(
        Book.objects.annotate(
            chapter_count=Count("chapters"),
            word_count=Coalesce(Sum("chapters__word_count"), 0),
        ).order_by("id")
    )


def get_book(conn_or_slug, slug: str | None = None):
    from onechapter.reader.models import Book

    init_db()
    actual_slug = slug if _is_conn(conn_or_slug) else conn_or_slug
    if not actual_slug:
        return None
    return (
        Book.objects.annotate(
            chapter_count=Count("chapters"),
            word_count=Coalesce(Sum("chapters__word_count"), 0),
        )
        .filter(slug=actual_slug)
        .first()
    )


def get_book_by_id(conn_or_id, book_id: int | None = None):
    from onechapter.reader.models import Book

    init_db()
    actual_id = book_id if _is_conn(conn_or_id) else conn_or_id
    return Book.objects.filter(id=actual_id).first()


def list_chapters(conn_or_book_id, book_id: int | None = None):
    from onechapter.reader.models import Chapter

    init_db()
    actual_book_id = book_id if _is_conn(conn_or_book_id) else conn_or_book_id
    return list(Chapter.objects.filter(book_id=actual_book_id).order_by("position"))


def get_chapter(conn_or_book_id, book_id_or_pos: int, position: int | None = None):
    from onechapter.reader.models import Chapter

    init_db()
    if _is_conn(conn_or_book_id) and position is not None:
        actual_book_id = book_id_or_pos
        actual_pos = position
    else:
        actual_book_id = conn_or_book_id
        actual_pos = book_id_or_pos
    return Chapter.objects.filter(book_id=actual_book_id, position=actual_pos).first()


def upsert_book_from_split(
    conn_or_split,
    split_arg: SplitResult | None = None,
    *,
    slug: str | None = None,
    filename: str | None = None,
    language: str | None = None,
    source_sha256: str | None = None,
    s3_key: str | None = None,
    uploaded_by=None,
    allow_replace: bool = False,
):
    from onechapter.reader.models import Book

    init_db()
    split: SplitResult = split_arg if _is_conn(conn_or_split) else conn_or_split  # type: ignore[assignment]
    source_sha256 = source_sha256 or None
    s3_key = s3_key or None

    if source_sha256 and not allow_replace:
        by_hash = Book.objects.filter(source_sha256=source_sha256).first()
        if by_hash:
            return get_book(by_hash.slug)

    target_slug = slug or slugify(split.title)
    base = target_slug
    n = 2

    with transaction.atomic():
        while True:
            existing = Book.objects.filter(slug=target_slug).first()
            if not existing:
                break
            if allow_replace:
                _replace_book_in_place(
                    existing,
                    split,
                    filename=filename,
                    language=language,
                    source_sha256=source_sha256,
                    s3_key=s3_key,
                )
                return get_book(existing.slug)
            # Non-destructive upload: if canonical Tarasov or identical source filename/hash
            # is already in the library, return the existing book without overwriting it.
            if (
                split.profile == "tarasov"
                and existing.slug == "tehnologiya-zhizni"
            ) or (
                source_sha256 and existing.source_sha256 == source_sha256
            ) or (
                filename
                and existing.source_filename == filename
                and existing.title == split.title
            ):
                return get_book(existing.slug)
            target_slug = f"{base}-{n}"
            n += 1

        book = Book.objects.create(
            slug=target_slug,
            title=split.title,
            author=split.author,
            language=language,
            profile=split.profile,
            source_filename=filename,
            source_sha256=source_sha256,
            s3_key=s3_key,
            uploaded_by=uploaded_by,
        )
        _insert_chapters(book, split)
    return get_book(book.slug)


def _insert_chapters(book, split: SplitResult) -> None:
    from onechapter.reader.models import Chapter

    keyed = compute_chapter_keys(split.chapters)
    Chapter.objects.bulk_create(
        [
            Chapter(
                book=book,
                position=ch.index,
                chapter_key=chapter_key,
                content_sha256=content_hash,
                part=ch.part,
                title=ch.title,
                body=ch.body,
                word_count=ch.words,
            )
            for ch, chapter_key, content_hash in keyed
        ]
    )


def _replace_book_in_place(
    book,
    split: SplitResult,
    *,
    filename: str | None = None,
    language: str | None = None,
    source_sha256: str | None = None,
    s3_key: str | None = None,
) -> None:
    """Update a book and its chapters in place by chapter_key so Chapter IDs and Reflections survive."""
    from onechapter.reader.models import Chapter, Reflection

    book.title = split.title
    book.author = split.author
    book.profile = split.profile
    if filename is not None:
        book.source_filename = filename
    if language is not None:
        book.language = language
    if source_sha256 is not None:
        book.source_sha256 = source_sha256 or None
    if s3_key is not None:
        book.s3_key = s3_key or None
    book.save()

    existing_chapters = list(Chapter.objects.filter(book=book))
    by_key = {ch.chapter_key: ch for ch in existing_chapters}
    by_hash: dict[str, list[Chapter]] = {}
    for ch in existing_chapters:
        if ch.content_sha256:
            by_hash.setdefault(ch.content_sha256, []).append(ch)

    # Shift existing positions and chapter_keys out of the target range inside the
    # transaction to avoid temporary unique constraint collisions when reordering or renaming.
    for idx, ch in enumerate(existing_chapters):
        ch.position = 100_000 + idx
        ch.chapter_key = f"__tmp_{ch.id}_{idx}"
        ch.save(update_fields=["position", "chapter_key"])

    keyed = compute_chapter_keys(split.chapters)
    kept_ids: set[int] = set()
    final_chapters_by_key: dict[str, Chapter] = {}
    final_chapters_by_id: dict[int, Chapter] = {}

    for draft, chapter_key, content_hash in keyed:
        matched = by_key.get(chapter_key)
        if matched is None or matched.id in kept_ids:
            candidates = [c for c in by_hash.get(content_hash, []) if c.id not in kept_ids]
            matched = candidates[0] if candidates else None

        if matched is not None:
            matched.position = draft.index
            matched.chapter_key = chapter_key
            matched.content_sha256 = content_hash
            matched.part = draft.part
            matched.title = draft.title
            matched.body = draft.body
            matched.word_count = draft.words
            matched.save()
            kept_ids.add(matched.id)
            final_chapters_by_key[chapter_key] = matched
            final_chapters_by_id[matched.id] = matched
        else:
            created = Chapter.objects.create(
                book=book,
                position=draft.index,
                chapter_key=chapter_key,
                content_sha256=content_hash,
                part=draft.part,
                title=draft.title,
                body=draft.body,
                word_count=draft.words,
            )
            kept_ids.add(created.id)
            final_chapters_by_key[chapter_key] = created
            final_chapters_by_id[created.id] = created

    # Remove chapters that no longer exist in the split; Reflection.chapter uses
    # ON DELETE SET NULL so reflections remain intact with their snapshots.
    Chapter.objects.filter(book=book).exclude(id__in=kept_ids).delete()

    # Re-anchor any reflections for this book by surviving Chapter ID or chapter_key.
    for ref in Reflection.objects.filter(book=book):
        surviving_ch = final_chapters_by_id.get(ref.chapter_id) if ref.chapter_id else None
        if surviving_ch is not None:
            if ref.chapter_key != surviving_ch.chapter_key:
                ref.chapter_key = surviving_ch.chapter_key
                ref.save(update_fields=["chapter_key"])
            continue
        target_ch = final_chapters_by_key.get(ref.chapter_key)
        if target_ch is not None and ref.chapter_id != target_ch.id:
            ref.chapter = target_ch
            ref.save(update_fields=["chapter"])


def ingest_file(
    path: Path,
    conn=None,
    *,
    uploaded_by=None,
    allow_replace: bool = False,
    s3_client=None,
):
    from onechapter.reader.models import Book

    init_db()
    path = Path(path)
    raw_bytes = path.read_bytes()
    digest = storage.sha256_bytes(raw_bytes)

    if not allow_replace:
        existing_hash = Book.objects.filter(source_sha256=digest).first()
        if existing_hash:
            return get_book(existing_hash.slug)

    _, s3_key, temp_or_local = storage.store_original(
        raw_bytes,
        path.name,
        s3_client=s3_client,
    )
    cleanup_temp = temp_or_local != path and config.s3_bucket() != ""
    try:
        extracted = extract_path(path if path.exists() else temp_or_local)
        split = split_extracted(extracted)
        if not split.chapters:
            raise ValueError("No chapters found in that file.")
        language = "ru" if split.profile == "tarasov" else None
        slug = "tehnologiya-zhizni" if split.profile == "tarasov" else None
        return upsert_book_from_split(
            conn,
            split,
            slug=slug,
            filename=path.name,
            language=language,
            source_sha256=digest,
            s3_key=s3_key,
            uploaded_by=uploaded_by,
            allow_replace=allow_replace,
        )
    finally:
        if cleanup_temp and temp_or_local.exists():
            temp_or_local.unlink(missing_ok=True)


def reprocess_book(slug: str, *, s3_client=None):
    """Operator command: re-fetch the stored original from S3/uploads and re-split in place."""
    init_db()
    book = get_book(slug)
    if not book:
        raise ValueError(f"Unknown book slug: {slug}")
    if not book.s3_key and not book.source_filename:
        raise ValueError(f"Book '{slug}' has no stored original to reprocess.")

    key = book.s3_key or book.source_filename
    local_path, should_cleanup = storage.fetch_original_to_temp(key, s3_client=s3_client)
    try:
        extracted = extract_path(local_path)
        split = split_extracted(extracted)
        if not split.chapters:
            raise ValueError("No chapters found in that file.")
        language = book.language or ("ru" if split.profile == "tarasov" else None)
        return upsert_book_from_split(
            None,
            split,
            slug=book.slug,
            filename=book.source_filename,
            language=language,
            source_sha256=book.source_sha256,
            s3_key=book.s3_key,
            allow_replace=True,
        )
    finally:
        if should_cleanup and local_path.exists():
            local_path.unlink(missing_ok=True)


def save_upload(filename: str, data: bytes) -> Path:
    ensure_dirs()
    safe = storage.safe_filename(filename)
    stage_dir = config.DATA_DIR / "tmp" / f"upload-{uuid.uuid4().hex}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    dest = stage_dir / safe
    dest.write_bytes(data)
    return dest


def cleanup_staged_upload(path: Path) -> None:
    try:
        if path.exists():
            path.unlink(missing_ok=True)
        parent = path.parent
        if parent.name.startswith("upload-") and parent.exists():
            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass


def _ensure_django_user( reader, username_hint: str | None = None):
    if reader.user_id:
        return reader.user
    User = get_user_model()
    base = slugify(username_hint or reader.display_name or "reader") or "reader"
    username = f"{base}-{uuid.uuid4().hex[:8]}"
    user = User.objects.create_user(username=username)
    reader.user = user
    reader.save(update_fields=["user"])
    return user


def get_or_create_local_reader(conn=None):
    """Return the unified primary reader in local/single-operator mode, creating one if empty."""
    from onechapter.reader.models import Reader

    init_db()
    reader = Reader.objects.order_by("id").first()
    if reader:
        _ensure_django_user(reader, "reader")
        return reader
    reader = Reader.objects.create(
        display_name="Local reader",
        timezone=DEFAULT_TIMEZONE,
        delivery_hhmm=DEFAULT_DELIVERY,
        is_allowed=True,
        is_operator=True,
    )
    _ensure_django_user(reader, "local-reader")
    return reader


def get_or_create_channel_reader(
    channel: str,
    external_id: int | str,
    name: str | None = None,
    *,
    handle: str | None = None,
    invite_code: str | None = None,
):
    """Resolve or create a Reader for any messaging/login channel (telegram, signal, whatsapp, email)."""
    from onechapter.reader.models import ChannelIdentity, Reader

    init_db()
    ext_str = str(external_id)
    ident = (
        ChannelIdentity.objects.select_related("reader", "reader__user")
        .filter(channel=channel, external_id=ext_str)
        .first()
    )
    allowed_now = config.is_access_allowed(ext_str, invite_code)

    if ident:
        reader = ident.reader
        updated_fields: list[str] = []
        if name and reader.display_name != name:
            reader.display_name = name
            updated_fields.append("display_name")
        if allowed_now and not reader.is_allowed:
            reader.is_allowed = True
            updated_fields.append("is_allowed")
        if updated_fields:
            reader.save(update_fields=updated_fields)
        if handle and ident.handle != handle:
            ident.handle = handle
            ident.save(update_fields=["handle"])
        _ensure_django_user(reader, handle or name or f"{channel}-{ext_str}")
        return reader

    with transaction.atomic():
        # If there is a single initial local Reader with no identity on this channel yet,
        # and no other allowed identities exist for this channel, unify them into the same Reader!
        existing_channel_count = ChannelIdentity.objects.filter(
            channel=channel,
            reader__is_allowed=True,
        ).count()
        unlinked_reader = (
            Reader.objects.filter(is_allowed=True)
            .exclude(channels__channel=channel)
            .order_by("id")
            .first()
            if existing_channel_count == 0
            else None
        )
        has_explicit_gate = bool(config.allowed_telegram_ids() or config.invite_code())
        is_first_reader = not Reader.objects.filter(is_allowed=True).exists() and not (
            config.access_mode() == "invite" and has_explicit_gate
        )
        is_allowed = allowed_now or is_first_reader

        if unlinked_reader is not None and is_allowed:
            reader = unlinked_reader
            if name and (not reader.display_name or reader.display_name == "Local reader"):
                reader.display_name = name
                reader.save(update_fields=["display_name"])
        else:
            reader = Reader.objects.create(
                display_name=name or "",
                timezone=DEFAULT_TIMEZONE,
                delivery_hhmm=DEFAULT_DELIVERY,
                is_allowed=is_allowed,
                is_operator=is_first_reader or (str(external_id) in config.allowed_telegram_ids()),
            )

        _ensure_django_user(reader, handle or name or f"{channel}-{ext_str}")
        ChannelIdentity.objects.create(
            reader=reader,
            channel=channel,
            external_id=ext_str,
            handle=handle or "",
            is_delivery_enabled=True,
        )
        return reader


def get_or_create_telegram_reader(
    conn_or_tg_id,
    telegram_id_or_name: int | str | None = None,
    name: str | None = None,
    *,
    handle: str | None = None,
    invite_code: str | None = None,
):
    if _is_conn(conn_or_tg_id):
        tg_id = telegram_id_or_name
        actual_name = name
    else:
        tg_id = conn_or_tg_id
        actual_name = telegram_id_or_name if isinstance(telegram_id_or_name, str) or telegram_id_or_name is None else name
    return get_or_create_channel_reader(
        "telegram",
        tg_id,  # type: ignore[arg-type]
        actual_name,
        handle=handle,
        invite_code=invite_code,
    )


def resolve_web_reader(request):
    """Return the signed-in Reader, or the unified primary Reader when web login is not enforced."""
    from onechapter.reader.models import Reader

    init_db()
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        profile = Reader.objects.filter(user=user).first()
        if profile:
            return profile
    if config.require_web_login():
        return None
    return get_or_create_local_reader()


def update_reader(
    conn_or_reader_id,
    reader_id: int | None = None,
    *,
    timezone: str | None = None,
    delivery_hhmm: str | None = None,
    name: str | None = None,
) -> None:
    from onechapter.reader.models import Reader

    init_db()
    actual_id = reader_id if _is_conn(conn_or_reader_id) else conn_or_reader_id
    reader = Reader.objects.filter(id=actual_id).first()
    if not reader:
        return
    if timezone and is_valid_timezone(timezone):
        reader.timezone = timezone.strip()
    if delivery_hhmm:
        normalized = normalize_hhmm(delivery_hhmm)
        if normalized:
            reader.delivery_hhmm = normalized
    if name is not None:
        reader.display_name = name
    reader.save()


def get_plan(conn_or_reader_id, reader_id_or_book_id: int, book_id: int | None = None):
    from onechapter.reader.models import Plan

    init_db()
    if _is_conn(conn_or_reader_id) and book_id is not None:
        r_id = reader_id_or_book_id
        b_id = book_id
    else:
        r_id = conn_or_reader_id
        b_id = reader_id_or_book_id
    return (
        Plan.objects.select_related("reader", "book")
        .filter(reader_id=r_id, book_id=b_id)
        .first()
    )


def active_plan_for_reader(conn_or_reader_id, reader_id: int | None = None):
    from onechapter.reader.models import Plan

    init_db()
    r_id = reader_id if _is_conn(conn_or_reader_id) else conn_or_reader_id
    return (
        Plan.objects.select_related("reader", "book")
        .filter(reader_id=r_id, status__in=["active", "paused"])
        .annotate(
            status_order=Case(
                When(status="active", then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("status_order", "-id")
        .first()
    )


def list_active_plans(conn=None):
    from onechapter.reader.models import ChannelIdentity, Plan

    init_db()
    plans = list(
        Plan.objects.select_related("reader", "book")
        .filter(
            status="active",
            reader__is_allowed=True,
            reader__channels__channel="telegram",
            reader__channels__is_delivery_enabled=True,
        )
        .distinct()
    )
    reader_ids = [p.reader_id for p in plans]
    tg_map = {
        ci.reader_id: int(ci.external_id)
        for ci in ChannelIdentity.objects.filter(
            reader_id__in=reader_ids,
            channel="telegram",
            is_delivery_enabled=True,
        )
        if ci.external_id.lstrip("-").isdigit()
    }
    for p in plans:
        p.reader._prefetched_telegram_id = tg_map.get(p.reader_id)
    return [p for p in plans if p.telegram_id is not None]


def chapter_count(conn_or_book_id, book_id: int | None = None) -> int:
    from onechapter.reader.models import Chapter

    init_db()
    actual_book_id = book_id if _is_conn(conn_or_book_id) else conn_or_book_id
    return Chapter.objects.filter(book_id=actual_book_id).count()


def state_for_plan(conn_or_plan, plan_row=None, now: datetime | None = None) -> PlanState:
    init_db()
    actual_plan = plan_row if _is_conn(conn_or_plan) else conn_or_plan
    total = chapter_count(None, actual_plan["book_id"])
    return plan_state(actual_plan, total, now)


def start_book(
    conn_or_reader_id,
    reader_id_or_book_id: int,
    book_id: int | None = None,
    now: datetime | None = None,
) -> PlanState:
    from onechapter.reader.models import Plan, Reader

    init_db()
    if _is_conn(conn_or_reader_id) and book_id is not None:
        r_id = reader_id_or_book_id
        b_id = book_id
    else:
        r_id = conn_or_reader_id
        b_id = reader_id_or_book_id

    with transaction.atomic():
        reader = Reader.objects.get(id=r_id)
        today = today_in(reader.timezone, now)
        Plan.objects.filter(
            reader_id=r_id,
            status__in=["active", "paused"],
        ).exclude(book_id=b_id).update(status="idle", paused_on=None)

        plan, _ = Plan.objects.update_or_create(
            reader_id=r_id,
            book_id=b_id,
            defaults={
                "status": "active",
                "started_on": today,
                "paused_on": None,
            },
        )
    plan = get_plan(None, r_id, b_id)
    return state_for_plan(None, plan, now)


def pause_plan(conn_or_plan_id, plan_id: int | None = None, now: datetime | None = None) -> None:
    from onechapter.reader.models import Plan

    init_db()
    p_id = plan_id if _is_conn(conn_or_plan_id) else conn_or_plan_id
    plan = Plan.objects.select_related("reader").filter(id=p_id).first()
    if not plan or plan.status != "active":
        return
    today = today_in(plan.reader.timezone, now)
    plan.status = "paused"
    plan.paused_on = today
    plan.save(update_fields=["status", "paused_on"])


def resume_plan(conn_or_plan_id, plan_id: int | None = None, now: datetime | None = None) -> None:
    from onechapter.reader.models import Plan

    init_db()
    p_id = plan_id if _is_conn(conn_or_plan_id) else conn_or_plan_id
    plan = Plan.objects.select_related("reader").filter(id=p_id).first()
    if not plan or plan.status != "paused" or not plan.started_on or not plan.paused_on:
        return
    today = today_in(plan.reader.timezone, now)
    started = plan.started_on if isinstance(plan.started_on, date) else date.fromisoformat(str(plan.started_on))
    paused = plan.paused_on if isinstance(plan.paused_on, date) else date.fromisoformat(str(plan.paused_on))
    shift = (today - paused).days
    new_start = started.fromordinal(started.toordinal() + shift)
    plan.status = "active"
    plan.started_on = new_start
    plan.paused_on = None
    plan.save(update_fields=["status", "started_on", "paused_on"])


def chapter_views(conn_or_book_id, book_id_or_state, state_arg: PlanState | None = None) -> list[ChapterView]:
    if _is_conn(conn_or_book_id):
        b_id = book_id_or_state
        state = state_arg
    else:
        b_id = conn_or_book_id
        state = book_id_or_state

    unlocked = state.unlocked_through if state and state.status in {"active", "paused", "finished"} else -1
    if state and state.finished:
        unlocked = state.total - 1
    views = []
    for row in list_chapters(None, b_id):
        views.append(
            ChapterView(
                position=row.position,
                title=row.title,
                part=row.part,
                body=row.body,
                word_count=row.word_count,
                unlocked=row.position <= unlocked,
            )
        )
    return views


def record_delivery(
    conn_or_plan_id,
    plan_id_or_date,
    local_date_or_pos,
    position_or_channel,
    channel: str | None = None,
) -> bool:
    from onechapter.reader.models import Delivery

    init_db()
    if _is_conn(conn_or_plan_id) and channel is not None:
        p_id = plan_id_or_date
        loc_date = local_date_or_pos
        pos = position_or_channel
        chan = channel
    else:
        p_id = conn_or_plan_id
        loc_date = plan_id_or_date
        pos = local_date_or_pos
        chan = position_or_channel

    parsed_date = loc_date if isinstance(loc_date, date) else date.fromisoformat(str(loc_date))
    try:
        with transaction.atomic():
            Delivery.objects.create(
                plan_id=p_id,
                local_date=parsed_date,
                chapter_position=int(pos),
                channel=str(chan),
            )
        return True
    except IntegrityError:
        return False


def already_delivered(
    conn_or_plan_id,
    plan_id_or_date,
    local_date_or_channel,
    channel: str | None = None,
) -> bool:
    from onechapter.reader.models import Delivery

    init_db()
    if _is_conn(conn_or_plan_id) and channel is not None:
        p_id = plan_id_or_date
        loc_date = local_date_or_channel
        chan = channel
    else:
        p_id = conn_or_plan_id
        loc_date = plan_id_or_date
        chan = local_date_or_channel

    parsed_date = loc_date if isinstance(loc_date, date) else date.fromisoformat(str(loc_date))
    return Delivery.objects.filter(
        plan_id=p_id,
        local_date=parsed_date,
        channel=str(chan),
    ).exists()


def add_reflection(
    conn_or_plan_id,
    plan_id_or_pos: int,
    position_or_body: int | str,
    body: str | None = None,
    *,
    source_channel: str = "web",
    kind: str = "text",
    audio_s3_key: str | None = None,
    audio_mime_type: str | None = None,
    audio_duration_sec: int | None = None,
    external_media_id: str | None = None,
    transcript_text: str | None = None,
):
    from onechapter.reader.models import Chapter, Plan, Reflection

    init_db()
    if _is_conn(conn_or_plan_id) and body is not None:
        p_id = plan_id_or_pos
        pos = int(position_or_body)
        text = body
    else:
        p_id = int(conn_or_plan_id)
        pos = int(plan_id_or_pos)
        text = str(position_or_body)

    plan = Plan.objects.select_related("reader", "book").get(id=p_id)
    chapter = Chapter.objects.filter(book_id=plan.book_id, position=pos).first()
    chapter_key = chapter.chapter_key if chapter else f"pos-{pos}"
    chapter_title = chapter.title if chapter else f"Day {pos + 1}"

    return Reflection.objects.create(
        reader=plan.reader,
        book=plan.book,
        plan=plan,
        chapter=chapter,
        chapter_key=chapter_key,
        chapter_position_snapshot=pos,
        chapter_title_snapshot=chapter_title,
        kind=kind,
        source_channel=source_channel,
        body=text.strip(),
        audio_s3_key=audio_s3_key,
        audio_mime_type=audio_mime_type,
        audio_duration_sec=audio_duration_sec,
        external_media_id=external_media_id,
        transcript_text=transcript_text,
    )


def reflections_for(conn_or_plan_id, plan_id_or_pos: int, position: int | None = None):
    """Return reflections for a reader's book chapter (matching by chapter or chapter_key so reprocess/re-reads survive)."""
    from onechapter.reader.models import Chapter, Plan, Reflection

    init_db()
    if _is_conn(conn_or_plan_id) and position is not None:
        p_id = plan_id_or_pos
        pos = position
    else:
        p_id = conn_or_plan_id
        pos = plan_id_or_pos

    plan = Plan.objects.filter(id=p_id).first()
    if not plan:
        return []
    chapter = Chapter.objects.filter(book_id=plan.book_id, position=pos).first()
    if chapter:
        return list(
            Reflection.objects.filter(
                reader_id=plan.reader_id,
                book_id=plan.book_id,
            )
            .filter(Q(chapter=chapter) | Q(chapter_key=chapter.chapter_key))
            .order_by("created_at")
        )
    return list(
        Reflection.objects.filter(
            reader_id=plan.reader_id,
            book_id=plan.book_id,
            chapter_position_snapshot=pos,
        ).order_by("created_at")
    )


def bot_token(conn=None) -> str:
    from onechapter.config import env_bot_token
    from onechapter.db import setting

    return env_bot_token() or setting(None, "telegram_bot_token", "")


def set_bot_token(conn_or_token, token: str | None = None) -> None:
    from onechapter.db import set_setting

    actual_token = token if _is_conn(conn_or_token) else conn_or_token
    set_setting(None, "telegram_bot_token", (actual_token or "").strip())


def estimate_days(word_count_value: int) -> str:
    return f"{word_count_value} words"
