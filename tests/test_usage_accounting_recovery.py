"""Regression tests for Usage accounting persistence and initialization."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from typing import Any, cast

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

import custom_components.extended_openai_conversation_responses.usage as usage_module
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    UsageRequest,
    UsageRun,
    UsageTotals,
    _usage_request_from_backup,
    _usage_run_from_backup,
    async_get_usage,
)


class FakeStorage:
    """Small in-memory stand-in for Home Assistant Store."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)
        self.load_calls = 0
        self.save_calls = 0

    async def async_load(self):
        self.load_calls += 1
        return deepcopy(self.data)

    async def async_save(self, data):
        self.save_calls += 1
        self.data = deepcopy(data)


class FlakyLoadStorage(FakeStorage):
    def __init__(self, data=None) -> None:
        super().__init__(data)
        self.fail_next_load = True

    async def async_load(self):
        self.load_calls += 1
        if self.fail_next_load:
            self.fail_next_load = False
            raise OSError("temporary read failure")
        return deepcopy(self.data)


def _request(timestamp: str, request_id: str = "request-1") -> dict:
    return asdict(
        UsageRequest(
            request_id=request_id,
            run_id="run-1",
            timestamp=timestamp,
            agent_subentry_id="agent-1",
            provider="openai",
            model="gpt-test",
            api_mode="responses",
            successful=True,
            duration_ms=10,
            total_tokens=3,
        )
    )


async def test_concurrent_usage_acquisition_publishes_one_manager(monkeypatch) -> None:
    """Overlapping callers initialize and retain one authoritative manager."""
    started = asyncio.Event()
    release = asyncio.Event()
    stores = []

    class BlockingStore(FakeStorage):
        def __init__(self, _hass, _version, key, **_kwargs) -> None:
            super().__init__()
            self.key = key
            stores.append(self)

        async def async_load(self):
            self.load_calls += 1
            if self.key.endswith(".daily"):
                started.set()
                await release.wait()
            return deepcopy(self.data)

    monkeypatch.setattr(usage_module, "Store", BlockingStore)
    hass = SimpleNamespace(data={})

    first = asyncio.create_task(async_get_usage(hass, "entry-1", "agent-1"))
    await asyncio.wait_for(started.wait(), timeout=1)
    second = asyncio.create_task(async_get_usage(hass, "entry-1", "agent-1"))
    await asyncio.sleep(0)

    assert len(stores) == 3
    release.set()
    first_manager, second_manager = await asyncio.gather(first, second)

    assert first_manager is second_manager
    assert sum(store.load_calls for store in stores) == 3


async def test_initialization_failure_is_transactional_and_retryable() -> None:
    """A failed detail load leaves no partial or duplicate live state."""
    primary = FakeStorage(asdict(UsageTotals(api_request_count=4, total_tokens=12)))
    daily = FakeStorage({"days": {}})
    detail = FlakyLoadStorage(
        {
            "requests": [_request(usage_module.dt_util.utcnow().isoformat())],
            "runs": [],
        }
    )
    manager = UsageManager(primary, daily, detail, agent_subentry_id="agent-1")

    try:
        await manager.async_initialize()
    except OSError:
        pass
    else:
        raise AssertionError("first initialization should fail")

    assert manager._initialized is False
    assert manager.totals == UsageTotals()
    assert manager.daily == {}
    assert manager.requests == []

    await manager.async_initialize()

    assert manager._initialized is True
    assert manager.totals.api_request_count == 4
    assert manager.totals.total_tokens == 12
    assert len(manager.requests) == 1
    assert detail.load_calls == 2


async def test_combined_aggregate_snapshot_is_authoritative_over_legacy_mirror() -> None:
    """A committed aggregate snapshot survives a stale legacy totals mirror."""
    primary = FakeStorage(
        asdict(
            UsageTotals(
                api_request_count=1,
                successful_request_count=1,
                total_tokens=5,
            )
        )
    )
    daily = FakeStorage(
        {
            "totals": asdict(
                UsageTotals(
                    api_request_count=2,
                    successful_request_count=2,
                    total_tokens=17,
                )
            ),
            "days": {},
        }
    )
    manager = UsageManager(primary, daily)

    await manager.async_initialize()

    assert manager.totals.api_request_count == 2
    assert manager.totals.total_tokens == 17
    assert primary.load_calls == 0


async def test_request_daily_accounting_is_durable_before_run_finalizes() -> None:
    """A completed provider request is persisted before its user run ends."""
    primary = FakeStorage()
    daily = FakeStorage()
    details = FakeStorage()
    manager = UsageManager(
        primary, daily, details, agent_subentry_id="agent-1"
    )
    await manager.async_initialize()

    async with manager.async_run():
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=7, output_tokens=2, total_tokens=9),
            provider="openai",
            model="gpt-test",
            api_mode="responses",
        )

        assert daily.data["totals"]["api_request_count"] == 1
        assert daily.data["totals"]["conversation_count"] == 0
        persisted_day = next(iter(daily.data["days"].values()))
        assert persisted_day["api_request_count"] == 1
        assert persisted_day["total_tokens"] == 9
        assert persisted_day["run_count"] == 0

        reloaded = UsageManager(primary, daily, details, agent_subentry_id="agent-1")
        await reloaded.async_initialize()
        reloaded_day = next(iter(reloaded.daily.values()))
        assert reloaded.totals.api_request_count == 1
        assert reloaded_day["api_request_count"] == 1
        assert reloaded_day["run_count"] == 0

    final_day = next(iter(manager.daily.values()))
    assert final_day["run_count"] == 1
    assert final_day["api_request_count"] == 1
    assert final_day["total_tokens"] == 9


async def test_request_day_uses_home_assistant_local_calendar_date(monkeypatch) -> None:
    """Daily request accounting crosses midnight using HA local time."""
    manager = UsageManager(FakeStorage(), FakeStorage())
    await manager.async_initialize()
    completed_at = datetime(2026, 6, 1, 23, 30, tzinfo=UTC)
    dublin = ZoneInfo("Europe/Dublin")

    monkeypatch.setattr(usage_module.dt_util, "utcnow", lambda: completed_at)
    monkeypatch.setattr(
        usage_module.dt_util,
        "as_local",
        lambda value: value.astimezone(dublin),
    )

    await manager.async_record_request(
        successful=True,
        usage=RequestUsage(total_tokens=4),
    )

    assert manager.summary_for_date("2026-06-01")["api_request_count"] == 0
    assert manager.summary_for_date("2026-06-02")["api_request_count"] == 1
    assert manager.summary_for_date("2026-06-02")["total_tokens"] == 4


async def test_one_run_with_multiple_provider_requests_counts_each_once() -> None:
    """User-turn and provider-request counters remain independent."""
    manager = UsageManager(FakeStorage(), FakeStorage(), FakeStorage())
    await manager.async_initialize()

    async with manager.async_run():
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(total_tokens=5),
            provider="openai",
            model="model-a",
            api_mode="responses",
        )
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(total_tokens=7),
            provider="other",
            model="model-b",
            api_mode="chat",
        )

    day = next(iter(manager.daily.values()))
    assert day["run_count"] == 1
    assert day["api_request_count"] == 2
    assert day["successful_request_count"] == 2
    assert day["total_tokens"] == 12
    assert day["provider_breakdown"] == {"openai": 5, "other": 7}
    assert day["model_breakdown"] == {"model-a": 5, "model-b": 7}


async def test_failed_request_and_failed_run_are_each_recorded_once() -> None:
    """Failure accounting does not duplicate provider or user-turn events."""
    manager = UsageManager(FakeStorage(), FakeStorage(), FakeStorage())
    await manager.async_initialize()

    try:
        async with manager.async_run():
            await manager.async_record_request(
                successful=False,
                provider="openai",
                error_type="APIError",
            )
            raise RuntimeError("turn failed")
    except RuntimeError:
        pass

    day = next(iter(manager.daily.values()))
    assert manager.totals.api_request_count == 1
    assert manager.totals.failed_request_count == 1
    assert manager.totals.conversation_count == 1
    assert day["api_request_count"] == 1
    assert day["failed_request_count"] == 1
    assert day["run_count"] == 1
    assert day["failed_run_count"] == 1


async def test_detail_rows_do_not_reconstruct_or_double_count_aggregates() -> None:
    """Bounded detail storage is never treated as the aggregate source of truth."""
    now = usage_module.dt_util.utcnow().isoformat()
    daily = FakeStorage(
        {
            "totals": asdict(
                UsageTotals(
                    api_request_count=1,
                    successful_request_count=1,
                    total_tokens=3,
                )
            ),
            "days": {},
        }
    )
    details = FakeStorage(
        {
            "requests": [
                _request(now, "request-1"),
                _request(now, "request-2"),
            ],
            "runs": [],
        }
    )
    manager = UsageManager(FakeStorage(), daily, details, agent_subentry_id="agent-1")

    await manager.async_initialize()

    assert len(manager.requests) == 2
    assert manager.totals.api_request_count == 1
    assert manager.totals.total_tokens == 3



async def test_concurrent_provider_requests_are_counted_once_in_one_run() -> None:
    """Concurrent provider completions remain one run with exact request totals."""
    manager = UsageManager(FakeStorage(), FakeStorage(), FakeStorage())
    await manager.async_initialize()

    async with manager.async_run() as run:
        await asyncio.gather(
            *(
                manager.async_record_request(
                    successful=True,
                    usage=RequestUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    provider="openai",
                    model="gpt-test",
                    api_mode="responses",
                )
                for _ in range(12)
            )
        )

    day = next(iter(manager.daily.values()))
    assert manager.totals.conversation_count == 1
    assert manager.totals.api_request_count == 12
    assert manager.totals.total_tokens == 24
    assert run.request_count == 12
    assert run.total_tokens == 24
    assert day["run_count"] == 1
    assert day["api_request_count"] == 12
    assert day["total_tokens"] == 24
    assert len(manager.requests) == 12
    assert len(manager.runs) == 1



async def test_failed_initialization_replaces_published_manager_with_shared_fallback(
    monkeypatch,
) -> None:
    """A failed published persistent manager cannot survive beside the fallback."""
    hass = SimpleNamespace(data={})
    key = ("entry", "agent")
    published_manager = object()
    calls = 0

    async def failing_getter(_hass, entry_id: str, subentry_id: str):
        nonlocal calls
        calls += 1
        persistent_managers = hass.data.setdefault(usage_module._USAGE_MANAGERS, {})
        persistent_managers[(entry_id, subentry_id)] = published_manager
        raise OSError("usage store unavailable")

    monkeypatch.setattr(usage_module, "async_get_durable_usage", failing_getter)

    manager = await usage_module.async_get_usage(hass, *key)
    same_manager = await usage_module.async_get_usage(hass, *key)

    assert manager is same_manager
    assert calls == 1
    assert key not in hass.data[usage_module._USAGE_MANAGERS]
    assert hass.data[usage_module._VOLATILE_USAGE_MANAGERS][key] is manager
    assert key in hass.data[usage_module._USAGE_GETTER_LOCKS]


async def test_effective_getter_serializes_durable_selection(monkeypatch) -> None:
    """Concurrent callers cannot race persistent and fallback manager selection."""
    hass = SimpleNamespace(data={})
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def durable_getter(_hass, _entry_id: str, _subentry_id: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        entered.set()
        await release.wait()
        active -= 1
        return object()

    monkeypatch.setattr(usage_module, "async_get_durable_usage", durable_getter)

    first = asyncio.create_task(usage_module.async_get_usage(hass, "entry", "agent"))
    await entered.wait()
    second = asyncio.create_task(usage_module.async_get_usage(hass, "entry", "agent"))
    await asyncio.sleep(0)

    assert max_active == 1
    release.set()
    await asyncio.gather(first, second)
    assert max_active == 1



def _timestamp_timestamp_request(request_id: str, timestamp: object) -> dict:
    return asdict(
        UsageRequest(
            request_id=request_id,
            run_id="run-1",
            timestamp=timestamp,  # type: ignore[arg-type]
            agent_subentry_id="agent",
            provider="openai",
            model="gpt-test",
            api_mode="responses",
            successful=True,
            duration_ms=10,
        )
    )


def _timestamp_timestamp_run(run_id: str, started_at: object) -> dict:
    return asdict(
        UsageRun(
            run_id=run_id,
            started_at=started_at,  # type: ignore[arg-type]
            completed_at=None,
            duration_ms=10,
            agent_subentry_id="agent",
            home_assistant_conversation_id=None,
            source_device_id=None,
        )
    )


@pytest.mark.asyncio
async def test_initialize_drops_malformed_detail_timestamps_without_losing_valid_rows() -> None:
    """Corrupt request/run timestamps must not abort Usage initialization."""
    now = dt_util.utcnow().isoformat()
    details = FakeStorage(
        {
            "requests": [
                _timestamp_request("valid-request", now),
                _timestamp_request("bad-string-request", "not-a-timestamp"),
                _timestamp_request("bad-type-request", None),
            ],
            "runs": [
                _timestamp_run("valid-run", now),
                _timestamp_run("bad-string-run", "not-a-timestamp"),
                _timestamp_run("bad-type-run", None),
            ],
        }
    )
    manager = UsageManager(
        FakeStorage(),
        FakeStorage(),
        details,
        agent_subentry_id="agent",
    )

    await manager.async_initialize()

    assert [request.request_id for request in manager.requests] == ["valid-request"]
    assert [run.run_id for run in manager.runs] == ["valid-run"]


@pytest.mark.parametrize("invalid", ["not-a-timestamp", "2026-99-99T99:99:99"])
def test_backup_validation_still_rejects_malformed_timestamps(invalid: str) -> None:
    """Recovery semantics must not turn malformed backup timestamps into valid data."""
    with pytest.raises(ValueError, match="usage request metadata is invalid"):
        _usage_request_from_backup(_timestamp_request("bad-request", invalid), "agent")

    with pytest.raises(ValueError, match="usage run metadata is invalid"):
        _usage_run_from_backup(_timestamp_run("bad-run", invalid), "agent")



async def test_runtime_usage_aggregate_stores_use_atomic_writes(
    hass: HomeAssistant,
) -> None:
    """The authoritative aggregate snapshot and legacy mirror are crash-safe."""
    manager = await async_get_usage(hass, "entry-a", "agent-a")

    totals_store = cast(Any, manager._storage)
    daily_store = cast(Any, manager._daily_storage)

    assert totals_store._atomic_writes is True
    assert daily_store._atomic_writes is True
