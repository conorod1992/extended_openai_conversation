"""Tests for bounded management Usage and Conversation history results."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ArchiveSession,
    ArchiveTurn,
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.management_history_queries import (
    archive_get_page,
    archive_list_page,
    archive_search_page,
    usage_breakdowns,
    usage_daily_page,
    usage_runs_page,
    usage_summary,
)
from custom_components.extended_openai_conversation_responses.management_history_runtime import (
    wrap_management_history_bounds,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageRun,
)


class _Storage:
    async def async_load_metadata(self):
        return None

    async def async_save_metadata(self, data):
        return None

    async def async_load_partition(self, partition):
        return None

    async def async_save_partition(self, partition, data):
        return None


class _TrackedRuns:
    """Sequence that records how far reversed paging actually consumes."""

    def __init__(self, items):
        self.items = list(items)
        self.consumed = 0

    def __reversed__(self):
        for item in reversed(self.items):
            self.consumed += 1
            yield item


class _Usage:
    def __init__(self, *, runs=None, daily=None, details=None):
        self.runs = runs if runs is not None else []
        self.requests = []
        self.daily = daily or {}
        self._details = details or {}

    @property
    def latest_run(self):
        try:
            return self.runs.items[-1]
        except AttributeError:
            return self.runs[-1] if self.runs else None

    def as_dict(self):
        return {
            "conversation_count": 1,
            "api_request_count": 1,
            "successful_request_count": 1,
            "failed_request_count": 0,
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "details": dict(self._details),
        }

    def today_summary(self):
        return dict(next(iter(self.daily.values()), _day("2026-01-01")))

    def month_summary(self):
        return dict(next(iter(self.daily.values()), _day("2026-01")))


def _day(date: str, *, providers=None, models=None, modes=None):
    return {
        "date": date,
        "run_count": 1,
        "successful_run_count": 1,
        "failed_run_count": 0,
        "api_request_count": 1,
        "successful_request_count": 1,
        "failed_request_count": 0,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "tool_call_count": 0,
        "web_search_run_count": 0,
        "total_run_duration_ms": 1,
        "average_requests_per_completed_run": 1,
        "average_duration_ms_per_completed_run": 1,
        "provider_breakdown": providers or {},
        "model_breakdown": models or {},
        "api_mode_breakdown": modes or {},
    }


def _run(index: int) -> UsageRun:
    timestamp = f"2026-01-01T00:{index:02d}:00+00:00"
    return UsageRun(
        run_id=f"run-{index}",
        started_at=timestamp,
        completed_at=timestamp,
        duration_ms=index,
        agent_subentry_id="agent",
        home_assistant_conversation_id=None,
        source_device_id=None,
    )


def _timestamp(index: int) -> str:
    return (
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    ).isoformat()


def _session(index: int, *, turn_count: int = 0) -> ArchiveSession:
    stamp = _timestamp(index)
    return ArchiveSession(
        session_id=f"session-{index}",
        home_assistant_conversation_id=None,
        agent_subentry_id="agent",
        scope_id="user:alice",
        scope_type="user",
        scope_source="test",
        source_device_id=None,
        started_at=stamp,
        last_message_at=stamp,
        title=f"Conversation {index}",
        turn_count=turn_count,
        retention_state="retained",
    )


def _turn(session_id: str, index: int, text: str = "alpha") -> ArchiveTurn:
    return ArchiveTurn(
        turn_id=f"turn-{index}",
        session_id=session_id,
        run_id=None,
        timestamp=_timestamp(index),
        user_text=f"{text} user {index}",
        assistant_text=f"{text} assistant {index}",
        successful=True,
    )


@pytest.mark.asyncio
async def test_usage_run_page_consumes_only_offset_page_and_sentinel() -> None:
    tracked = _TrackedRuns([_run(index) for index in range(30)])
    result = usage_runs_page(_Usage(runs=tracked), offset=5, limit=3)

    assert [item["run_id"] for item in result["runs"]] == [
        "run-24",
        "run-23",
        "run-22",
    ]
    assert tracked.consumed == 9
    assert result == {
        "runs": result["runs"],
        "offset": 5,
        "limit": 3,
        "returned": 3,
        "has_more": True,
        "next_offset": 8,
    }


def test_usage_daily_and_breakdown_dimensions_are_explicitly_bounded() -> None:
    providers = {f"provider-{index}": index + 1 for index in range(105)}
    days = {
        f"2026-01-0{index}": _day(
            f"2026-01-0{index}", providers=providers if index == 1 else {}
        )
        for index in range(1, 5)
    }
    usage = _Usage(daily=days, details=providers)

    daily = usage_daily_page(
        usage,
        start_date="2026-01-01",
        end_date="2026-01-04",
        offset=1,
        limit=2,
    )
    assert [item["date"] for item in daily["days"]] == [
        "2026-01-02",
        "2026-01-03",
    ]
    assert daily["has_more"] is True
    assert daily["next_offset"] == 3

    summary = usage_summary(usage)
    assert len(summary["lifetime"]["details"]) == 100
    assert summary["lifetime"]["details_meta"]["has_more"] is True
    assert summary["lifetime"]["details_meta"]["omitted_contributions"] == 5

    breakdowns = usage_breakdowns(usage)
    assert len(breakdowns["providers"]) == 100
    assert breakdowns["breakdown_meta"]["providers"]["has_more"] is True
    assert breakdowns["breakdown_meta"]["providers"]["omitted_contributions"] == 5


@pytest.mark.asyncio
async def test_archive_list_search_and_turn_reads_return_real_pages() -> None:
    archive = ConversationArchive(_Storage(), "agent")
    await archive.async_initialize()
    archive._sessions = {_session(index).session_id: _session(index) for index in range(70)}

    listed = await archive_list_page(archive, "user:alice", offset=40, limit=20)
    assert listed["total"] == 70
    assert listed["returned"] == 20
    assert listed["has_more"] is True
    assert listed["next_offset"] == 60
    assert listed["sessions"][0]["session_id"] == "session-29"
    assert listed["sessions"][-1]["session_id"] == "session-10"

    searchable = _session(100, turn_count=30)
    archive._sessions = {searchable.session_id: searchable}
    archive._turns = defaultdict(
        list,
        {searchable.session_id: [_turn(searchable.session_id, index) for index in range(30)]},
    )
    found = await archive_search_page(
        archive,
        "user:alice",
        "alpha",
        offset=10,
        limit=5,
    )
    assert found["total"] == 30
    assert found["returned"] == 5
    assert found["has_more"] is True
    assert found["next_offset"] == 15
    assert [item["turn_id"] for item in found["results"]] == [
        "turn-19",
        "turn-18",
        "turn-17",
        "turn-16",
        "turn-15",
    ]

    long_session = _session(200, turn_count=45)
    archive._sessions = {long_session.session_id: long_session}
    archive._turns = defaultdict(
        list,
        {long_session.session_id: [_turn(long_session.session_id, index) for index in range(45)]},
    )
    turns = await archive_get_page(
        archive,
        "user:alice",
        long_session.session_id,
        start_turn=0,
        limit=999,
    )
    assert turns["limit"] == 20
    assert turns["returned"] == 20
    assert turns["total"] == 45
    assert turns["has_more"] is True
    assert turns["next_offset"] == 20


@pytest.mark.asyncio
async def test_optimized_overview_usage_is_reprojected_through_bounds(monkeypatch) -> None:
    providers = {f"provider-{index}": index + 1 for index in range(105)}
    usage = _Usage(daily={"2026-01-01": _day("2026-01-01", providers=providers)}, details=providers)
    original = AsyncMock(
        return_value={
            "agent": {"title": "Jarvis"},
            "usage": {"lifetime": {"details": providers}},
            "conversations": {},
        }
    )

    from custom_components.extended_openai_conversation_responses import (
        management_history_runtime as runtime,
    )

    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", lambda *_: (object(), object()))
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value=usage))
    wrapped = wrap_management_history_bounds(original)

    result = await wrapped(
        None,
        "admin",
        True,
        {
            "section": "overview",
            "action": "summary",
            "entry_id": "entry",
            "subentry_id": "agent",
        },
    )

    assert len(result["usage"]["lifetime"]["details"]) == 100
    assert result["usage"]["lifetime"]["details_meta"]["has_more"] is True
    original.assert_awaited_once()
