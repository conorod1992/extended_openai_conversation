"""Focused edge-case coverage for bounded management history queries."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

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
    usage_requests_page,
    usage_summary,
)
from custom_components.extended_openai_conversation_responses.management_result_limits import (
    MANAGEMENT_ARCHIVE_TURN_PAGE_MAX,
    MANAGEMENT_USAGE_BREAKDOWN_KEYS,
)
from custom_components.extended_openai_conversation_responses.usage import UsageRequest


class _Storage:
    async def async_load_metadata(self):
        return None

    async def async_save_metadata(self, data):
        return None

    async def async_load_partition(self, partition):
        return None

    async def async_save_partition(self, partition, data):
        return None


class _SummaryUsage:
    def __init__(self, details):
        self._details = details
        self.latest_run = None

    def as_dict(self):
        return {"details": self._details}

    def today_summary(self):
        return {"provider_breakdown": {}, "model_breakdown": {}, "api_mode_breakdown": {}}

    def month_summary(self):
        return {"provider_breakdown": {}, "model_breakdown": {}, "api_mode_breakdown": {}}


def _day(*, providers=None, models=None, modes=None):
    return {
        "provider_breakdown": providers if providers is not None else {},
        "model_breakdown": models if models is not None else {},
        "api_mode_breakdown": modes if modes is not None else {},
    }


def _stamp(index: int) -> str:
    return (
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    ).isoformat()


def _session(
    index: int,
    *,
    scope_id: str = "user:alice",
    retention_state: str = "retained",
    turn_count: int = 0,
) -> ArchiveSession:
    stamp = _stamp(index)
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


def _turn(session_id: str, index: int, text: str) -> ArchiveTurn:
    return ArchiveTurn(
        turn_id=f"turn-{index}",
        session_id=session_id,
        run_id=None,
        timestamp=_stamp(index),
        user_text=text,
        assistant_text=f"assistant {text}",
        successful=True,
    )


def _request(request_id: str, run_id: str, details: dict[object, object]) -> UsageRequest:
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
    malformed = usage_summary(_SummaryUsage(["not", "a", "mapping"]))
    assert malformed["lifetime"]["details"] == {}
    assert malformed["lifetime"]["details_meta"] == {
        "returned": 0,
        "has_more": False,
        "omitted_contributions": 0,
        "omitted_total": 0,
    }

    colliding = usage_summary(
        _SummaryUsage({1: 3, "1": 4, "negative": -5, "boolean": True})
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
            _request("other", "other-run", {"ignored": 99}),
            _request("wanted", "run-1", details),
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
            "2026-01-01": _day(providers={"outside-before": 100}),
            "2026-01-02": _day(
                providers={1: 3, "1": 4, "negative": -5, "boolean": True},
                models=["corrupt"],
            ),
            "2026-01-03": _day(providers=overflowing),
            "2026-01-04": _day(providers={"outside-after": 100}),
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
    newest = _session(30)
    second = _session(20)
    older = _session(10)
    foreign = _session(40, scope_id="user:bob")
    expired = _session(50, retention_state="expired")
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
    session = _session(100, turn_count=6)
    foreign = _session(101, scope_id="user:bob", turn_count=1)
    expired = _session(102, retention_state="expired", turn_count=1)
    archive._sessions = {
        session.session_id: session,
        foreign.session_id: foreign,
        expired.session_id: expired,
    }
    archive._turns = defaultdict(
        list,
        {
            session.session_id: [
                _turn(session.session_id, 4, "alpha newest"),
                _turn(session.session_id, 3, "alpha second"),
                _turn(session.session_id, 2, "alpha older"),
                _turn(session.session_id, 1, "unrelated text"),
            ],
            foreign.session_id: [_turn(foreign.session_id, 3, "alpha foreign")],
            expired.session_id: [_turn(expired.session_id, 3, "alpha expired")],
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
