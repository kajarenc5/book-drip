from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from onechapter.config import DEFAULT_DELIVERY, DEFAULT_TIMEZONE


@dataclass
class ChapterView:
    position: int
    title: str
    part: str | None
    body: str
    word_count: int
    unlocked: bool


@dataclass
class PlanState:
    plan_id: int
    reader_id: int
    book_id: int
    status: str
    started_on: date | None
    paused_on: date | None
    timezone: str
    delivery_hhmm: str
    today: date
    elapsed_days: int
    unlocked_through: int
    total: int
    finished: bool

    @property
    def today_position(self) -> int | None:
        if self.status != "active" or self.started_on is None:
            return None
        if self.finished:
            return self.total - 1
        return self.unlocked_through


def parse_date(value: str | date | None) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def is_valid_timezone(tz_name: str) -> bool:
    if not tz_name or not tz_name.strip():
        return False
    try:
        ZoneInfo(tz_name.strip())
        return True
    except Exception:
        return False


def _safe_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo((tz_name or DEFAULT_TIMEZONE).strip())
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def normalize_hhmm(value: str) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hh = int(match.group(1))
    mm = int(match.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def today_in(tz_name: str, now: datetime | None = None) -> date:
    tz = _safe_zoneinfo(tz_name)
    current = now.astimezone(tz) if now else datetime.now(tz)
    return current.date()


def local_now(tz_name: str, now: datetime | None = None) -> datetime:
    tz = _safe_zoneinfo(tz_name)
    return now.astimezone(tz) if now else datetime.now(tz)


def compute_elapsed(started_on: date, today: date, paused_on: date | None) -> int:
    if paused_on and paused_on <= today:
        end = paused_on
    else:
        end = today
    return max(0, (end - started_on).days)


def unlocked_through(elapsed_days: int, total: int) -> int:
    if total <= 0:
        return -1
    return min(elapsed_days, total - 1)


def is_finished(elapsed_days: int, total: int) -> bool:
    return total > 0 and elapsed_days >= total - 1 and total > 0


def plan_state(row, total: int, now: datetime | None = None) -> PlanState:
    tz = row["timezone"] or DEFAULT_TIMEZONE
    today = today_in(tz, now)
    started = parse_date(row["started_on"])
    paused = parse_date(row["paused_on"]) if row["status"] == "paused" else None
    elapsed = compute_elapsed(started, today, paused) if started else 0
    unlocked = unlocked_through(elapsed, total) if started else -1
    return PlanState(
        plan_id=row["id"],
        reader_id=row["reader_id"],
        book_id=row["book_id"],
        status=row["status"],
        started_on=started,
        paused_on=paused,
        timezone=tz,
        delivery_hhmm=row["delivery_hhmm"] or DEFAULT_DELIVERY,
        today=today,
        elapsed_days=elapsed,
        unlocked_through=unlocked,
        total=total,
        finished=bool(started) and elapsed >= max(total - 1, 0) and total > 0,
    )


def due_for_delivery(state: PlanState, now: datetime | None = None) -> bool:
    if state.status != "active" or state.started_on is None or state.total == 0:
        return False
    current = local_now(state.timezone, now)
    hh, mm = _parse_hhmm(state.delivery_hhmm)
    delivery_dt = datetime(
        current.year, current.month, current.day, hh, mm, tzinfo=current.tzinfo
    )
    return current >= delivery_dt


def _parse_hhmm(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return 8, 0
    return int(match.group(1)) % 24, int(match.group(2)) % 60


def add_days(value: date, days: int) -> date:
    return value + timedelta(days=days)
