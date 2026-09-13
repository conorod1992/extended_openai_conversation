"""Residual branch coverage for usage accounting."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import usage


class MemoryStorage:
    """Small in-memory UsageStorage implementation for residual tests."""

    def __init__(self, data=None):
        self.data = deepcopy(data)
        self.saves: list[dict] = []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves.append(deepcopy(data))


async def _manager(
    *,
    storage: MemoryStorage | None = None,
    daily_storage: MemoryStorage | None = None,
    detail_storage: MemoryStorage | None = None,
    request_retention_days: int = 30,
    run_retention_days: int = 30,
) -> usage.UsageManager:
    manager = usage.UsageManager(
        storage or MemoryStorage(),
        daily_storage,
        detail_storage,
        agent_subentry_id="agent",
        request_retention_days=request_retention_days,
        run_retention_days=run_retention_days,
    )
    await manager.async_initialize()
    return manager


def _request(
    request_id: str,
    timestamp: str,
    *,
    run_id: str = "run",
) -> usage.UsageRequest:
    return usage.UsageRequest(
        request_id=request_id,
        run_id=run_id,
        timestamp=timestamp,
        agent_subentry_id="agent",
        provider="openai",
        model="gpt-test",
        api_mode="responses",
        successful=True,
        duration_ms=1,
    )


def _run(run_id: str, started_at: str) -> usage.UsageRun:
    return usage.UsageRun(
        run_id=run_id,
        started_at=started_at,
        completed_at=started_at,
        duration_ms=1,
        agent_subentry_id="agent",
        home_assistant_conversation_id=None,
        source_device_id=None,
    )


def test_extract_usage_ignores_unmappable_detail_objects() -> None:
    """Opaque SDK detail objects still contribute named fields without iteration."""

    class OpaqueDetails:
        __slots__ = ("cached_tokens",)

        def __init__(self) -> None:
            self.cached_tokens = 7

    result = usage.extract_usage(
        {
            "input_tokens": 11,
            "output_tokens": 3,
            "input_tokens_details": OpaqueDetails(),
            "output_tokens_details": object(),
        }
    )

    assert result.input_tokens == 11
    assert result.output_tokens == 3
    assert result.total_tokens == 14
    assert result.cached_input_tokens == 7
    assert result.reasoning_tokens == 0
    assert result.details == {}


async def test_live_run_zero_retention_deduplicates_metadata_and_preserves_failure() -> None:
    """Zero retention must not change aggregate/run accounting semantics."""
    manager = await _manager(
        daily_storage=MemoryStorage(),
        detail_storage=MemoryStorage(),
        request_retention_days=0,
        run_retention_days=0,
    )

    async with manager.async_run() as run:
        await manager.async_record_request(
            successful=True,
            usage=usage.RequestUsage(input_tokens=3, output_tokens=2, total_tokens=5),
            provider="openai",
            model="gpt-test",
            api_mode="responses",
            tool_calls_requested=-4,
        )
        await manager.async_record_request(
            successful=False,
            usage=usage.RequestUsage(total_tokens=2),
            provider="openai",
            model="gpt-test",
            api_mode="responses",
            error_type=None,
            web_search_used=True,
        )

        assert run.request_count == 2
        assert run.successful_request_count == 1
        assert run.failed_request_count == 1
        assert run.tool_call_count == 0
        assert run.models == ["gpt-test"]
        assert run.providers == ["openai"]
        assert run.api_modes == ["responses"]
        assert run.web_search_used is True
        assert run.successful is False
        assert run.error_type == "provider_error"
        assert manager.requests == []

    assert manager.runs == []
    assert manager.totals.conversation_count == 1
    assert manager.totals.api_request_count == 2
    assert manager.totals.failed_request_count == 1


async def test_positive_retention_prune_keeps_recent_records_and_persists() -> None:
    """Exercise the positive-retention side of both prune comprehensions."""
    detail_storage = MemoryStorage()
    manager = await _manager(
        detail_storage=detail_storage,
        request_retention_days=30,
        run_retention_days=30,
    )
    now = dt_util.utcnow()
    manager.requests = [
        _request("recent", (now - timedelta(days=2)).isoformat()),
        _request("old", (now - timedelta(days=60)).isoformat()),
    ]
    manager.runs = [
        _run("recent", (now - timedelta(days=2)).isoformat()),
        _run("old", (now - timedelta(days=60)).isoformat()),
    ]

    result = await manager.async_prune_details()

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert [item.request_id for item in manager.requests] == ["recent"]
    assert [item.run_id for item in manager.runs] == ["recent"]
    assert [item["request_id"] for item in detail_storage.data["requests"]] == [
        "recent"
    ]
    assert [item["run_id"] for item in detail_storage.data["runs"]] == ["recent"]


async def test_clear_and_detail_save_are_noops_without_detail_storage() -> None:
    """No detail store is a supported configuration, not an error path."""
    manager = await _manager()
    now = dt_util.utcnow().isoformat()
    manager.requests = [_request("request", now)]
    manager.runs = [_run("run", now)]

    result = await manager.async_clear_details(confirm=True)
    await manager._async_save_details()

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []


def _minimal_backup(*, date: str) -> dict:
    return {
        "totals": asdict(usage.UsageTotals()),
        "daily": {date: usage._empty_day(date)},
        "requests": [],
        "runs": [],
    }


def test_backup_rejects_iso_length_date_that_is_not_a_real_calendar_date() -> None:
    """Reach the datetime parser failure after the length check succeeds."""
    with pytest.raises(ValueError, match="daily usage date is invalid"):
        usage.UsageManager.validate_backup_data(
            _minimal_backup(date="2026-99-99"), "target-agent"
        )


@pytest.mark.parametrize(
    "mutator, match",
    [
        (lambda day: day.pop("run_count"), "daily usage record is invalid"),
        (
            lambda day: day.__setitem__("average_requests_per_completed_run", True),
            "daily usage value average_requests_per_completed_run is invalid",
        ),
        (
            lambda day: day.__setitem__("average_duration_ms_per_completed_run", -0.1),
            "daily usage value average_duration_ms_per_completed_run is invalid",
        ),
    ],
)
def test_usage_day_validation_rejects_residual_invalid_shapes(mutator, match) -> None:
    day = usage._empty_day("2026-09-13")
    mutator(day)

    with pytest.raises(ValueError, match=match):
        usage._validate_usage_day("2026-09-13", day)


async def test_replace_backup_without_detail_store_uses_aggregate_only_path() -> None:
    """Restoring totals/days must work when bounded detail persistence is absent."""
    primary = MemoryStorage()
    manager = await _manager(storage=primary)
    day = usage._empty_day("2026-09-13")
    day["total_tokens"] = 9
    totals = usage.UsageTotals(total_tokens=9)

    await manager.async_replace_backup(totals, {"2026-09-13": day}, [], [])

    assert manager.totals.total_tokens == 9
    assert manager.daily["2026-09-13"]["total_tokens"] == 9
    assert primary.data["total_tokens"] == 9
    assert manager.requests == []
    assert manager.runs == []


def test_request_day_omits_empty_breakdown_labels() -> None:
    """Blank provider/model/mode values must not create meaningless buckets."""
    day = usage._empty_day("2026-09-13")

    usage._add_request_to_day(
        day,
        successful=True,
        usage=usage.RequestUsage(total_tokens=4),
        provider="",
        model="",
        api_mode="",
    )

    assert day["api_request_count"] == 1
    assert day["total_tokens"] == 4
    assert day["provider_breakdown"] == {}
    assert day["model_breakdown"] == {}
    assert day["api_mode_breakdown"] == {}


def test_merge_day_ignores_date_averages_and_non_integer_scalars() -> None:
    """Monthly aggregation only sums counters and nested breakdowns."""
    target = usage._empty_day("2026-09")
    source = usage._empty_day("2026-09-13")
    source.update(
        {
            "run_count": 2,
            "total_tokens": 10,
            "average_tokens_per_completed_run": 5,
            "average_requests_per_completed_run": 1.5,
            "average_duration_ms_per_completed_run": 20.0,
            "provider_breakdown": {"openai": 10},
        }
    )

    usage._merge_day(target, source)

    assert target["date"] == "2026-09"
    assert target["run_count"] == 2
    assert target["total_tokens"] == 10
    assert target["average_tokens_per_completed_run"] == 0
    assert target["average_requests_per_completed_run"] == 0.0
    assert target["average_duration_ms_per_completed_run"] == 0.0
    assert target["provider_breakdown"] == {"openai": 10}
