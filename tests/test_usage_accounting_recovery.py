"""Regression tests for Usage accounting persistence and initialization."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import custom_components.extended_openai_conversation_responses.usage as usage_module
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    UsageRequest,
    UsageTotals,
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
