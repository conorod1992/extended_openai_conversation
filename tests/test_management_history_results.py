"""Tests for bounded management Usage and Conversation history results."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ArchiveSession,
    ArchiveTurn,
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.management_history_queries import (
    MAX_MANAGEMENT_HISTORY_OFFSET,
    _async_archive_query,
    archive_get_page,
    archive_list_page,
    archive_search_page,
    usage_breakdowns,
    usage_daily_page,
    usage_requests_page,
    usage_runs_page,
    usage_summary,
)
from custom_components.extended_openai_conversation_responses.management_result_limits import (
    MANAGEMENT_ARCHIVE_TURN_PAGE_MAX,
    MANAGEMENT_USAGE_BREAKDOWN_KEYS,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageRequest,
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
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)).isoformat()


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
        "2025-12-31": _day("2025-12-31"),
        "2026-01-04": _day("2026-01-04"),
        "2026-01-02": _day("2026-01-02"),
        "2026-01-01": _day("2026-01-01", providers=providers),
        "2026-01-03": _day("2026-01-03"),
        "2026-01-05": _day("2026-01-05"),
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
    archive._sessions = {
        _session(index).session_id: _session(index) for index in range(70)
    }

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
        {
            searchable.session_id: [
                _turn(searchable.session_id, index) for index in range(30)
            ]
        },
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
        {
            long_session.session_id: [
                _turn(long_session.session_id, index) for index in range(45)
            ]
        },
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


async def test_optimized_overview_usage_is_bounded_at_source(
    hass, management_agent, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
        management_ui,
    )

    providers = {f"provider-{index}": index + 1 for index in range(105)}
    usage = _Usage(
        daily={"2026-01-01": _day("2026-01-01", providers=providers)}, details=providers
    )
    get_usage = AsyncMock(return_value=usage)
    monkeypatch.setattr(loading, "async_get_usage", get_usage)
    for name in ("async_get_memory", "async_get_knowledge", "async_get_guest_mode"):
        monkeypatch.setattr(
            loading, name, AsyncMock(side_effect=RuntimeError("unavailable"))
        )

    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "overview",
            "action": "summary",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )
    assert len(result["usage"]["lifetime"]["details"]) == 100
    assert result["usage"]["lifetime"]["details_meta"]["has_more"] is True
    get_usage.assert_awaited_once()


class _EdgeSummaryUsage:
    def __init__(self, details):
        self._details = details
        self.latest_run = None

    def as_dict(self):
        return {"details": self._details}

    def today_summary(self):
        return {"provider_breakdown": {}, "model_breakdown": {}, "api_mode_breakdown": {}}

    def month_summary(self):
        return {"provider_breakdown": {}, "model_breakdown": {}, "api_mode_breakdown": {}}


def _edge_day(*, providers=None, models=None, modes=None):
    return {
        "provider_breakdown": providers if providers is not None else {},
        "model_breakdown": models if models is not None else {},
        "api_mode_breakdown": modes if modes is not None else {},
    }


def _edge_stamp(index: int) -> str:
    return (
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
    ).isoformat()


def _edge_session(
    index: int,
    *,
    scope_id: str = "user:alice",
    retention_state: str = "retained",
    turn_count: int = 0,
) -> ArchiveSession:
    stamp = _edge_stamp(index)
    return ArchiveSession(
        session_id=f"session-{index}",
        home_assistant_conversation_id=None,
        agent_subentry_id="agent",
        scope_id=scope_id,
        scope_type="user",
        scope_source="test",
        source_device_id=None,
        started_at=stamp,
        last_message_at=stamp,
        title=f"Conversation {index}",
        turn_count=turn_count,
        retention_state=retention_state,
    )


def _edge_turn(session_id: str, index: int, text: str) -> ArchiveTurn:
    return ArchiveTurn(
        turn_id=f"turn-{index}",
        session_id=session_id,
        run_id=None,
        timestamp=_edge_stamp(index),
        user_text=text,
        assistant_text=f"assistant {text}",
        successful=True,
    )


def _edge_request(request_id: str, run_id: str, details: dict[object, object]) -> UsageRequest:
    return UsageRequest(
        request_id=request_id,
        run_id=run_id,
        timestamp="2026-01-01T00:00:00+00:00",
        agent_subentry_id="agent",
        provider="openai",
        model="test-model",
        api_mode="responses",
        successful=True,
        duration_ms=10,
        details=details,  # type: ignore[arg-type]
    )


def test_usage_summary_salvages_non_mapping_and_colliding_detail_keys() -> None:
    """Management projection remains bounded and deterministic for malformed counters."""
    malformed = usage_summary(_EdgeSummaryUsage(["not", "a", "mapping"]))
    assert malformed["lifetime"]["details"] == {}
    assert malformed["lifetime"]["details_meta"] == {
        "returned": 0,
        "has_more": False,
        "omitted_contributions": 0,
        "omitted_total": 0,
    }

    colliding = usage_summary(
        _EdgeSummaryUsage({1: 3, "1": 4, "negative": -5, "boolean": True})
    )
    assert colliding["lifetime"]["details"] == {
        "1": 7,
        "negative": 0,
        "boolean": 0,
    }


def test_usage_requests_page_filters_and_bounds_request_details() -> None:
    """Only the requested run is returned and oversized detail maps are capped."""
    details = {f"detail-{index}": index + 1 for index in range(MANAGEMENT_USAGE_BREAKDOWN_KEYS + 2)}
    manager = SimpleNamespace(
        requests=[
            _edge_request("other", "other-run", {"ignored": 99}),
            _edge_request("wanted", "run-1", details),
        ]
    )

    result = usage_requests_page(manager, "run-1", limit=50, offset=0)

    assert [item["request_id"] for item in result["requests"]] == ["wanted"]
    projected = result["requests"][0]
    assert len(projected["details"]) == MANAGEMENT_USAGE_BREAKDOWN_KEYS
    assert projected["details_meta"]["has_more"] is True
    assert projected["details_meta"]["omitted_contributions"] == 2
    assert projected["details_meta"]["omitted_total"] == sum(
        range(MANAGEMENT_USAGE_BREAKDOWN_KEYS + 1, MANAGEMENT_USAGE_BREAKDOWN_KEYS + 3)
    )
    assert result["has_more"] is False


def test_usage_breakdowns_filter_dates_and_salvage_malformed_dimensions() -> None:
    """Breakdown aggregation skips out-of-window/corrupt data and accounts for overflow."""
    overflowing = {
        f"provider-{index}": index + 1
        for index in range(MANAGEMENT_USAGE_BREAKDOWN_KEYS + 1)
    }
    manager = SimpleNamespace(
        daily={
            "2026-01-01": _edge_day(providers={"outside-before": 100}),
            "2026-01-02": _edge_day(
                providers={1: 3, "1": 4, "negative": -5, "boolean": True},
                models=["corrupt"],
            ),
            "2026-01-03": _edge_day(providers=overflowing),
            "2026-01-04": _edge_day(providers={"outside-after": 100}),
        }
    )

    result = usage_breakdowns(
        manager, start_date="2026-01-02", end_date="2026-01-03"
    )

    assert "outside-before" not in result["providers"]
    assert "outside-after" not in result["providers"]
    assert result["providers"]["1"] == 7
    assert result["providers"]["negative"] == 0
    assert result["providers"]["boolean"] == 0
    assert result["models"] == {}
    assert result["breakdown_meta"]["providers"]["returned"] == MANAGEMENT_USAGE_BREAKDOWN_KEYS
    assert result["breakdown_meta"]["providers"]["has_more"] is True
    assert result["breakdown_meta"]["providers"]["omitted_contributions"] == 4
    assert result["breakdown_meta"]["providers"]["omitted_total"] > 0


@pytest.mark.asyncio
async def test_archive_page_validation_rejects_blank_search_and_extreme_offset() -> None:
    """Invalid management searches fail before retained archive state is touched."""
    archive = ConversationArchive(_Storage(), "agent")

    with pytest.raises(HomeAssistantError, match="query must not be blank"):
        await archive_search_page(archive, "user:alice", "   ")

    with pytest.raises(HomeAssistantError, match="offset must not exceed"):
        await archive_list_page(
            archive,
            "user:alice",
            offset=MAX_MANAGEMENT_HISTORY_OFFSET + 1,
        )


@pytest.mark.asyncio
async def test_archive_list_filters_scope_and_retention_without_displacing_newest() -> None:
    """Bounded ranking ignores inaccessible sessions and does not admit older tail rows."""
    archive = ConversationArchive(_Storage(), "agent")
    await archive.async_initialize()
    newest = _edge_session(30)
    second = _edge_session(20)
    older = _edge_session(10)
    foreign = _edge_session(40, scope_id="user:bob")
    expired = _edge_session(50, retention_state="expired")
    archive._sessions = {
        item.session_id: item
        for item in (newest, second, older, foreign, expired)
    }

    result = await archive_list_page(archive, "user:alice", limit=2)

    assert result["total"] == 3
    assert [item["session_id"] for item in result["sessions"]] == [
        newest.session_id,
        second.session_id,
    ]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_archive_search_applies_access_date_and_text_filters_with_bounded_ranking() -> None:
    """Search excludes inaccessible/out-of-window/nonmatching turns before ranking."""
    archive = ConversationArchive(_Storage(), "agent")
    await archive.async_initialize()
    session = _edge_session(100, turn_count=6)
    foreign = _edge_session(101, scope_id="user:bob", turn_count=1)
    expired = _edge_session(102, retention_state="expired", turn_count=1)
    archive._sessions = {
        session.session_id: session,
        foreign.session_id: foreign,
        expired.session_id: expired,
    }
    archive._turns = defaultdict(
        list,
        {
            session.session_id: [
                _edge_turn(session.session_id, 4, "alpha newest"),
                _edge_turn(session.session_id, 3, "alpha second"),
                _edge_turn(session.session_id, 2, "alpha older"),
                _edge_turn(session.session_id, 1, "unrelated text"),
            ],
            foreign.session_id: [_edge_turn(foreign.session_id, 3, "alpha foreign")],
            expired.session_id: [_edge_turn(expired.session_id, 3, "alpha expired")],
        },
    )

    result = await archive_search_page(
        archive,
        "user:alice",
        "alpha",
        start_date="2026-01-01",
        end_date="2026-01-01",
        limit=2,
    )

    assert result["total"] == 3
    assert [item["turn_id"] for item in result["results"]] == ["turn-4", "turn-3"]
    assert result["has_more"] is True

    outside = await archive_search_page(
        archive,
        "user:alice",
        "alpha",
        start_date="2026-01-02",
        end_date="2026-01-03",
        limit=2,
    )
    assert outside["results"] == []
    assert outside["total"] == 0


@pytest.mark.asyncio
async def test_async_archive_query_waits_for_worker_cleanup_when_cancelled() -> None:
    """Cancellation does not abandon the protected off-loop archive query."""
    started = Event()
    release = Event()

    class _Archive:
        def __init__(self):
            self._lock = asyncio.Lock()
            self.initialized_checks = 0

        def _ensure_initialized(self):
            self.initialized_checks += 1

    archive = _Archive()

    def query():
        started.set()
        assert release.wait(timeout=5)
        return "done"

    task = asyncio.create_task(_async_archive_query(archive, query))
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0)
    assert started.is_set()

    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert archive.initialized_checks == 1
    assert archive._lock.locked() is False


@pytest.mark.asyncio
async def test_archive_get_page_clamps_inputs_and_recovers_missing_total_metadata() -> None:
    """Turn pages use safe limits and a returned-count fallback for legacy-shaped results."""
    async_get = pytest.importorskip("unittest.mock").AsyncMock(
        return_value={"turns": [{"turn_id": "turn-1"}], "has_more": False}
    )
    archive = SimpleNamespace(async_get=async_get)

    result = await archive_get_page(
        archive,
        "user:alice",
        "session-1",
        start_turn=-5,
        limit=999,
    )

    async_get.assert_awaited_once_with(
        "user:alice", "session-1", 0, MANAGEMENT_ARCHIVE_TURN_PAGE_MAX
    )
    assert result["start_turn"] == 0
    assert result["limit"] == MANAGEMENT_ARCHIVE_TURN_PAGE_MAX
    assert result["returned"] == 1
    assert result["total"] == 1
    assert result["has_more"] is False
