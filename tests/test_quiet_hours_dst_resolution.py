"""Resolve exceptional wall-clock deadlines without changing device ownership."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    _next_discontinuous_transition,
    _parse_clock,
    _resolve_quiet_wall_time,
    quiet_period_for,
)


@pytest.mark.parametrize(
    ("zone_name", "day", "clock", "expected"),
    [
        ("Europe/Dublin", "2026-03-29", "01:15", "2026-03-29T02:00:00+01:00"),
        ("Europe/Dublin", "2026-03-29", "01:45", "2026-03-29T02:00:00+01:00"),
        ("Australia/Lord_Howe", "2026-10-04", "02:15", "2026-10-04T02:30:00+11:00"),
    ],
)
def test_skipped_boundaries_resolve_to_first_valid_instant(zone_name, day, clock, expected):
    assert _resolve_quiet_wall_time(
        datetime.fromisoformat(day).date(), _parse_clock(clock), ZoneInfo(zone_name)
    ).isoformat() == expected


def test_repeated_hour_uses_earliest_start_and_latest_end():
    zone = ZoneInfo("Europe/Dublin")
    day = datetime(2026, 10, 25).date()
    first = _resolve_quiet_wall_time(day, _parse_clock("01:30"), zone)
    last = _resolve_quiet_wall_time(day, _parse_clock("01:30"), zone, ending=True)
    assert first.astimezone(UTC) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert last.astimezone(UTC) == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    for minute in (0, 10, 30, 44):
        current = datetime(2026, 10, 25, 1, minute, tzinfo=UTC).astimezone(zone)
        period = quiet_period_for(current, "01:15", "01:45")
        assert period is not None
        assert period.start.astimezone(UTC) == datetime(2026, 10, 25, 0, 15, tzinfo=UTC)
        assert period.end.astimezone(UTC) == datetime(2026, 10, 25, 1, 45, tzinfo=UTC)
    for minute in (45, 59):
        now = datetime(2026, 10, 25, 1, minute, tzinfo=UTC).astimezone(zone)
        assert quiet_period_for(now, "01:15", "01:45") is None


def test_forward_gap_boundary_and_collapsed_interval_are_minute_precise():
    zone = ZoneInfo("Europe/Dublin")
    for minute in range(0, 61):
        now = datetime(2026, 3, 29, 0, 30, tzinfo=UTC) + timedelta(minutes=minute)
        period = quiet_period_for(now.astimezone(zone), "01:30", "03:30")
        assert (period is not None) is (minute >= 30)
        if period:
            assert period.start.isoformat() == "2026-03-29T02:00:00+01:00"
        # Both boundaries land on 02:00: never invent a transient period.
        assert quiet_period_for(now.astimezone(zone), "01:15", "01:45") is None


def test_overnight_end_in_gap_and_next_ordinary_day():
    zone = ZoneInfo("Europe/Dublin")
    before = datetime(2026, 3, 29, 0, 59, tzinfo=UTC).astimezone(zone)
    after = datetime(2026, 3, 29, 1, 0, tzinfo=UTC).astimezone(zone)
    assert quiet_period_for(before, "22:00", "01:30") is not None
    assert quiet_period_for(after, "22:00", "01:30") is None
    normal = datetime(2026, 3, 30, 0, 15, tzinfo=UTC).astimezone(zone)
    assert quiet_period_for(normal, "22:00", "01:30") is not None


def test_next_anomalous_deadlines_use_same_gap_and_fold_policy():
    zone = ZoneInfo("Europe/Dublin")
    start = datetime(2026, 3, 28, 12, tzinfo=UTC)
    due = _next_discontinuous_transition(
        start, _parse_clock("01:30"), zone, ending=False
    )
    assert due == datetime(2026, 3, 29, 1, tzinfo=UTC)
    due_end = _next_discontinuous_transition(
        datetime(2026, 10, 24, 12, tzinfo=UTC),
        _parse_clock("01:30"), zone, ending=True
    )
    assert due_end == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert _next_discontinuous_transition(
        start, _parse_clock("13:00"), zone, ending=False
    ) is None
