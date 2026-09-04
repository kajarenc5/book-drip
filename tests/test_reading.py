from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from onechapter.reading import compute_elapsed, due_for_delivery, plan_state, unlocked_through
from onechapter.telegram_format import format_chapter_messages


class FakeRow(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


def test_calendar_unlocks_one_chapter_per_day():
    assert compute_elapsed(date(2026, 9, 1), date(2026, 9, 1), None) == 0
    assert compute_elapsed(date(2026, 9, 1), date(2026, 9, 4), None) == 3
    assert unlocked_through(0, 78) == 0
    assert unlocked_through(77, 78) == 77
    assert unlocked_through(90, 78) == 77


def test_pause_freezes_elapsed_days():
    elapsed = compute_elapsed(date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 3))
    assert elapsed == 2


def test_due_after_delivery_time():
    row = FakeRow(
        id=1,
        reader_id=1,
        book_id=1,
        status="active",
        started_on="2026-09-01",
        paused_on=None,
        timezone="Europe/Moscow",
        delivery_hhmm="08:00",
    )
    morning = datetime(2026, 9, 4, 8, 1, tzinfo=ZoneInfo("Europe/Moscow"))
    night = datetime(2026, 9, 4, 7, 50, tzinfo=ZoneInfo("Europe/Moscow"))
    state = plan_state(row, 10, morning)
    assert due_for_delivery(state, morning)
    state_night = plan_state(row, 10, night)
    assert not due_for_delivery(state_night, night)


def test_telegram_chunks_long_chapter():
    body = ("Paragraph. " * 80 + "\n\n") * 20
    messages = format_chapter_messages(
        day=1,
        total=10,
        title="Test",
        part="Путь",
        body=body,
        next_title="Next",
        book_title="Book",
    )
    assert len(messages) > 1
    assert all(len(m) < 4096 for m in messages)
    assert "Tomorrow: Next" in messages[-1]
