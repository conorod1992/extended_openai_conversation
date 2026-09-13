"""Focused residual coverage for usage accounting and persistence."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import usage


class MemoryStorage:
    def __init__(self, data=None, *, fail_load: bool = False, fail_save: bool = False):
        self.data = deepcopy(data)
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.saves = []

    async def async_load(self):
        if self.fail_load:
            raise OSError("load failed")
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail_save:
            raise OSError("save failed")
        self.data = deepcopy(data)
        self.saves.append(deepcopy(data))


async def _manager(
    storage=None,
    daily_storage=None,
    detail_storage=None,
    **kwargs,
):
    manager = usage.UsageManager(
        storage or MemoryStorage(),
        daily_storage,
        detail_storage,
        agent_subentry_id="agent-target",
        **kwargs,
    )
    await manager.async_initialize()
    return manager


def _request(*, request_id="request-1", run_id="run-1", timestamp=None, **changes):
    values = {
        "request_id": request_id,
        "run_id": run_id,
        "timestamp": timestamp or dt_util.utcnow().isoformat(),
        "agent_subentry_id": "source-agent",
        "provider": "openai",
        "model": "gpt-test",
        "api_mode": "responses",
        "successful": True,
        "duration_ms": 10,
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "cached_input_tokens": 1,
        "reasoning_tokens": 0,
        "request_stage": "initial",
        "tool_calls_requested": 1,
        "web_search_used": False,
        "error_type": None,
        "details": {"input_cached_tokens": 1},
    }
    values.update(changes)
    return values


def _run(*, run_id="run-1", started_at=None, **changes):
    values = {
        "run_id": run_id,
        "started_at": started_at or dt_util.utcnow().isoformat(),
        "completed_at": dt_util.utcnow().isoformat(),
        "duration_ms": 20,
        "agent_subentry_id": "source-agent",
        "home_assistant_conversation_id": "conversation-1",
        "source_device_id": "device-1",
        "request_count": 1,
        "successful_request_count": 1,
        "failed_request_count": 0,
        "tool_call_count": 1,
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
        "cached_input_tokens": 1,
        "reasoning_tokens": 0,
        "successful": True,
        "models": ["gpt-test"],
        "providers": ["openai"],
        "api_modes": ["responses"],
        "web_search_used": False,
        "error_type": None,
    }
    values.update(changes)
    return values


def _backup():
    day = usage._empty_day("2026-09-13")
    day.update(
        {
            "run_count": 1,
            "successful_run_count": 1,
            "api_request_count": 1,
            "successful_request_count": 1,
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
            "cached_input_tokens": 1,
            "tool_call_count": 1,
            "total_run_duration_ms": 20,
            "average_tokens_per_completed_run": 5,
            "average_requests_per_completed_run": 1.0,
            "average_duration_ms_per_completed_run": 20.0,
            "provider_breakdown": {"openai": 5},
            "model_breakdown": {"gpt-test": 5},
            "api_mode_breakdown": {"responses": 5},
        }
    )
    return {
        "totals": asdict(
            usage.UsageTotals(
                conversation_count=1,
                api_request_count=1,
                successful_request_count=1,
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
                cached_input_tokens=1,
                details={"input_cached_tokens": 1},
            )
        ),
        "daily": {"2026-09-13": day},
        "requests": [_request()],
        "runs": [_run()],
    }


def test_extract_usage_salvages_object_and_model_dump_details() -> None:
    class Details:
        def __init__(self):
            self.cached_tokens = 4
            self.extra = 2

    class DumpDetails:
        def __init__(self):
            self.reasoning_tokens = 3

        def model_dump(self, *, exclude_none):
            assert exclude_none is True
            return {"reasoning_tokens": 3, "accepted_prediction_tokens": 2, "zero": 0}

    result = usage.extract_usage(
        SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=0,
            prompt_tokens_details=Details(),
            completion_tokens_details=DumpDetails(),
        )
    )

    assert result.total_tokens == 15
    assert result.cached_input_tokens == 4
    assert result.reasoning_tokens == 3
    assert result.details == {
        "input_cached_tokens": 4,
        "input_extra": 2,
        "output_reasoning_tokens": 3,
        "output_accepted_prediction_tokens": 2,
    }


def test_totals_storage_salvages_only_positive_integer_counters() -> None:
    totals = usage._totals_from_storage(
        {
            "totals": {
                "conversation_count": 2,
                "api_request_count": True,
                "successful_request_count": -1,
                "failed_request_count": "3",
                "input_tokens": 5,
                "details": {"good": 7, "zero": 0, "bad": "8", "negative": -1},
                "unknown": 99,
            }
        }
    )

    assert totals.conversation_count == 2
    assert totals.api_request_count == 0
    assert totals.successful_request_count == 0
    assert totals.failed_request_count == 0
    assert totals.input_tokens == 5
    assert totals.details == {"good": 7}
    assert usage._totals_from_storage(None) == usage.UsageTotals()
    assert usage._totals_from_storage({"totals": []}) == usage.UsageTotals()


async def test_initialize_salvages_valid_detail_records_and_applies_retention() -> None:
    now = dt_util.utcnow()
    detail_storage = MemoryStorage(
        {
            "requests": [
                _request(request_id="recent", timestamp=(now - timedelta(days=1)).isoformat()),
                {"bad": "request"},
                _request(request_id="old", timestamp=(now - timedelta(days=90)).isoformat()),
            ],
            "runs": [
                _run(run_id="recent", started_at=(now - timedelta(days=1)).isoformat()),
                {"bad": "run"},
                _run(run_id="old", started_at=(now - timedelta(days=90)).isoformat()),
            ],
        }
    )
    daily_storage = MemoryStorage(
        {
            "totals": {"conversation_count": 4, "details": {}},
            "days": {"2026-09-13": {"total_tokens": 9}, "bad": []},
        }
    )
    manager = await _manager(
        MemoryStorage({"conversation_count": 99}),
        daily_storage,
        detail_storage,
        request_retention_days=30,
        run_retention_days=30,
    )

    assert manager.totals.conversation_count == 4
    assert list(manager.daily) == ["2026-09-13"]
    assert [item.request_id for item in manager.requests] == ["recent"]
    assert [item.run_id for item in manager.runs] == ["recent"]
    await manager.async_initialize()
    assert [item.request_id for item in manager.requests] == ["recent"]


async def test_request_outside_run_updates_aggregates_without_detail_record() -> None:
    details = MemoryStorage()
    manager = await _manager(MemoryStorage(), MemoryStorage(), details)
    await manager.async_record_request(
        successful=False,
        usage=usage.RequestUsage(total_tokens=4),
        provider="provider",
        model="model",
        api_mode="chat",
        error_type="ProviderError",
    )

    assert manager.totals.api_request_count == 1
    assert manager.totals.failed_request_count == 1
    assert manager.requests == []
    assert details.saves == []
    assert manager.latest_run is None


async def test_mark_failed_clamps_error_and_finalize_is_idempotent() -> None:
    manager = await _manager()
    manager.mark_current_run_failed("ignored-without-run")

    async with manager.async_run() as run:
        manager.mark_current_run_failed("x" * 200)
        assert manager.current_run() is run
    assert manager.current_run() is None
    assert run.successful is False
    assert run.error_type == "x" * 128
    assert manager.totals.conversation_count == 1

    await manager._async_finalize_run(run)
    assert manager.totals.conversation_count == 1


async def test_prune_and_clear_details_cover_zero_retention_and_confirmation() -> None:
    details = MemoryStorage()
    manager = await _manager(
        MemoryStorage(),
        MemoryStorage(),
        details,
        request_retention_days=0,
        run_retention_days=0,
    )
    manager.requests = [usage.UsageRequest(**_request())]
    manager.runs = [usage.UsageRun(**_run())]

    result = await manager.async_prune_details(save=False)
    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert details.saves == []

    manager.requests = [usage.UsageRequest(**_request())]
    manager.runs = [usage.UsageRun(**_run())]
    with pytest.raises(ValueError, match="confirmation"):
        await manager.async_clear_details(confirm=False)
    result = await manager.async_clear_details(confirm=True)
    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert details.data == {"requests": [], "runs": []}


async def test_summary_series_pagination_breakdowns_and_listener_lifecycle() -> None:
    manager = await _manager()
    first_day = usage._empty_day("2026-09-01")
    first_day.update(
        {
            "run_count": 2,
            "api_request_count": 3,
            "total_tokens": 20,
            "total_run_duration_ms": 100,
            "provider_breakdown": {"openai": 20},
            "model_breakdown": {"gpt-a": 20},
            "api_mode_breakdown": {"responses": 20},
        }
    )
    usage._calculate_averages(first_day)
    second_day = usage._empty_day("2026-09-02")
    second_day.update(
        {
            "run_count": 1,
            "api_request_count": 1,
            "total_tokens": 5,
            "total_run_duration_ms": 50,
            "provider_breakdown": {"other": 5},
            "model_breakdown": {"gpt-b": 5},
            "api_mode_breakdown": {"chat": 5},
        }
    )
    usage._calculate_averages(second_day)
    manager.daily = {"2026-09-01": first_day, "2026-09-02": second_day}
    manager.runs = [
        usage.UsageRun(**_run(run_id="one", successful=True)),
        usage.UsageRun(**_run(run_id="two", successful=False)),
        usage.UsageRun(**_run(run_id="three", successful=True)),
    ]
    manager.requests = [
        usage.UsageRequest(**_request(request_id="a", run_id="one")),
        usage.UsageRequest(**_request(request_id="b", run_id="one")),
        usage.UsageRequest(**_request(request_id="c", run_id="two")),
    ]

    assert manager.summary_for_date("missing")["run_count"] == 0
    month = manager.month_summary("2026-09")
    assert month["run_count"] == 3
    assert month["total_tokens"] == 25
    assert month["average_requests_per_completed_run"] == pytest.approx(1.33)
    assert [d["date"] for d in manager.daily_series("2026-09-01", "2026-09-30", limit=1)] == ["2026-09-01"]

    page = manager.recent_runs(limit=1, offset=1, successful=True)
    assert [item["run_id"] for item in page["runs"]] == ["one"]
    assert page["has_more"] is False
    assert manager.recent_runs(limit=999, offset=-5)["limit"] == usage.MAX_RECENT_LIMIT

    requests = manager.requests_for_run("one", limit=1)
    assert requests["requests"][0]["request_id"] == "a"
    assert requests["has_more"] is True
    assert manager.requests_for_run("missing", offset=-2)["offset"] == 0

    assert manager.breakdowns("2026-09-02", "2026-09-02") == {
        "providers": {"other": 5},
        "models": {"gpt-b": 5},
        "api_modes": {"chat": 5},
    }
    assert manager.latest_run.run_id == "three"

    called = []
    remove = manager.async_add_listener(lambda: called.append(True))
    manager._notify()
    remove()
    manager._notify()
    assert called == [True]


async def test_backup_export_requires_initialization_and_returns_deep_copy() -> None:
    manager = usage.UsageManager(MemoryStorage())
    with pytest.raises(RuntimeError, match="not been initialized"):
        await manager.async_backup_data()

    await manager.async_initialize()
    manager.daily["2026-09-13"] = usage._empty_day("2026-09-13")
    exported = await manager.async_backup_data()
    exported["daily"]["2026-09-13"]["total_tokens"] = 999
    assert manager.daily["2026-09-13"]["total_tokens"] == 0


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda data: data.pop("runs"), "incomplete or corrupted"),
        (lambda data: data.__setitem__("totals", {}), "totals are invalid"),
        (lambda data: data["totals"].__setitem__("input_tokens", True), "counter input_tokens"),
        (lambda data: data["totals"].__setitem__("details", {"x": -1}), "total details"),
        (lambda data: data.__setitem__("daily", []), "daily usage data"),
        (lambda data: data["daily"].__setitem__("bad", usage._empty_day("bad")), "daily usage date"),
        (lambda data: data["daily"]["2026-09-13"].__setitem__("date", "2026-09-12"), "does not match"),
        (lambda data: data.__setitem__("requests", {}), "details must be lists"),
        (lambda data: data["requests"].append(deepcopy(data["requests"][0])), "request IDs must be unique"),
        (lambda data: data["runs"].append(deepcopy(data["runs"][0])), "run IDs must be unique"),
    ],
)
def test_validate_backup_rejects_corrupted_top_level_shapes(mutator, match) -> None:
    data = _backup()
    mutator(data)
    with pytest.raises(ValueError, match=match):
        usage.UsageManager.validate_backup_data(data, "target")


def test_validate_backup_rebinds_agent_and_accepts_valid_state() -> None:
    totals, daily, requests, runs = usage.UsageManager.validate_backup_data(
        _backup(), "target-agent"
    )
    assert totals.total_tokens == 5
    assert daily["2026-09-13"]["provider_breakdown"] == {"openai": 5}
    assert requests[0].agent_subentry_id == "target-agent"
    assert runs[0].agent_subentry_id == "target-agent"


@pytest.mark.parametrize(
    ("target", "change", "match"),
    [
        ("request", {"timestamp": "not-a-time"}, "request metadata"),
        ("request", {"successful": 1}, "request metadata"),
        ("request", {"duration_ms": -1}, "counter duration_ms"),
        ("request", {"details": {"bad": True}}, "request details"),
        ("run", {"started_at": "bad"}, "run metadata"),
        ("run", {"models": "gpt-test"}, "run metadata"),
        ("run", {"models": [1]}, "run metadata"),
        ("run", {"request_count": -1}, "counter request_count"),
    ],
)
def test_backup_detail_validators_reject_invalid_metadata(target, change, match) -> None:
    raw = _request(**change) if target == "request" else _run(**change)
    validator = usage._usage_request_from_backup if target == "request" else usage._usage_run_from_backup
    with pytest.raises(ValueError, match=match):
        validator(raw, "target")


def test_backup_detail_validators_reject_non_objects_and_unknown_fields() -> None:
    with pytest.raises(ValueError, match="request must be an object"):
        usage._usage_request_from_backup([], "target")
    with pytest.raises(ValueError, match="request is invalid"):
        usage._usage_request_from_backup({**_request(), "unknown": 1}, "target")
    with pytest.raises(ValueError, match="run must be an object"):
        usage._usage_run_from_backup([], "target")
    with pytest.raises(ValueError, match="run is invalid"):
        usage._usage_run_from_backup({**_run(), "unknown": 1}, "target")


async def test_replace_backup_persists_then_reapplies_retention_and_notifies() -> None:
    details = MemoryStorage()
    daily = MemoryStorage()
    manager = await _manager(
        MemoryStorage(),
        daily,
        details,
        request_retention_days=0,
        run_retention_days=0,
    )
    totals, days, requests, runs = usage.UsageManager.validate_backup_data(
        _backup(), "agent-target"
    )
    calls = []
    manager.async_add_listener(lambda: calls.append(True))

    await manager.async_replace_backup(totals, days, requests, runs)

    assert manager.totals.total_tokens == 5
    assert manager.requests == []
    assert manager.runs == []
    assert daily.data["totals"]["total_tokens"] == 5
    assert details.data == {"requests": [], "runs": []}
    assert calls == [True]


async def test_legacy_totals_mirror_failure_is_non_fatal(caplog) -> None:
    primary = MemoryStorage(fail_save=True)
    daily = MemoryStorage()
    manager = await _manager(primary, daily)

    await manager.async_record_conversation()

    assert daily.data["totals"]["conversation_count"] == 1
    assert "legacy usage totals mirror" in caplog.text


async def test_save_guards_and_listener_failures_do_not_escape(caplog) -> None:
    manager = usage.UsageManager(MemoryStorage())
    with pytest.raises(RuntimeError, match="not been initialized"):
        await manager._async_save_totals()
    with pytest.raises(RuntimeError, match="not been initialized"):
        await manager._async_save_aggregates()

    await manager.async_initialize()

    async def fail():
        raise OSError("disk full")

    await manager._async_save_safely("test data", fail)
    manager.async_add_listener(lambda: (_ for _ in ()).throw(RuntimeError("listener")))
    manager._notify()
    assert "Unable to persist usage test data" in caplog.text
    assert "Usage listener failed" in caplog.text


def test_time_day_and_merge_helpers_cover_edge_cases() -> None:
    naive = usage._parse_time("2026-09-13T12:00:00")
    aware = usage._parse_time("2026-09-13T12:00:00+00:00")
    invalid = usage._parse_time(None)
    assert naive.tzinfo is not None
    assert aware.tzinfo is not None
    assert invalid == usage.datetime.min.replace(tzinfo=dt_util.UTC)

    target = usage._empty_day("2026-09")
    source = usage._empty_day("2026-09-01")
    source.update(
        {
            "run_count": 2,
            "total_tokens": 11,
            "provider_breakdown": {"openai": 11},
            "average_requests_per_completed_run": 99.0,
        }
    )
    usage._merge_day(target, source)
    usage._calculate_averages(target)
    assert target["run_count"] == 2
    assert target["total_tokens"] == 11
    assert target["provider_breakdown"] == {"openai": 11}
    assert target["average_requests_per_completed_run"] == 0


async def test_async_get_usage_publishes_single_manager_before_initialization(monkeypatch) -> None:
    created = []

    class FakeStore(MemoryStorage):
        def __init__(self, hass, version, key, **kwargs):
            super().__init__()
            created.append((version, key, kwargs))

    real_initialize = usage.UsageManager.async_initialize
    entered = asyncio.Event()
    release = asyncio.Event()
    initialize_calls = 0

    async def blocked_initialize(self):
        nonlocal initialize_calls
        initialize_calls += 1
        entered.set()
        await release.wait()
        await real_initialize(self)

    monkeypatch.setattr(usage, "Store", FakeStore)
    monkeypatch.setattr(usage.UsageManager, "async_initialize", blocked_initialize)
    hass = SimpleNamespace(data={})

    first_task = asyncio.create_task(usage.async_get_usage(hass, "entry", "agent"))
    await entered.wait()
    second_task = asyncio.create_task(usage.async_get_usage(hass, "entry", "agent"))
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first is second
    assert initialize_calls == 2
    assert len(created) == 3
    assert created[2][2]["private"] is True
    assert created[2][2]["serialize_in_event_loop"] is False
    assert await usage.async_get_usage(hass, "entry", "agent") is first