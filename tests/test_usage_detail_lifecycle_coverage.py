"""Focused coverage for Usage detail retention and destructive lifecycle paths."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import logging

import pytest
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import usage as usage_module
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
    UsageRequest,
    UsageRun,
)



class DetailStorage:
    """Small detail-store stand-in with observable writes and optional failure."""

    def __init__(self) -> None:
        self.data = None
        self.saves: list[dict] = []
        self.fail_save = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        if self.fail_save:
            raise OSError("detail store unavailable")
        self.data = deepcopy(data)
        self.saves.append(deepcopy(data))


class TotalsStorage:
    async def async_load(self):
        return None

    async def async_save(self, _data) -> None:
        return None


def _request(request_id: str, timestamp: str) -> UsageRequest:
    return UsageRequest(
        request_id=request_id,
        run_id=f"run-{request_id}",
        timestamp=timestamp,
        agent_subentry_id="agent",
        provider="openai",
        model="gpt-test",
        api_mode="responses",
        successful=True,
        duration_ms=1,
    )


def _run(run_id: str, started_at: str) -> UsageRun:
    return UsageRun(
        run_id=run_id,
        started_at=started_at,
        completed_at=started_at,
        duration_ms=1,
        agent_subentry_id="agent",
        home_assistant_conversation_id=None,
        source_device_id=None,
    )


def _manager(
    detail_storage: DetailStorage | None,
    *,
    request_retention_days: int = 30,
    run_retention_days: int = 30,
) -> UsageManager:
    return UsageManager(
        TotalsStorage(),
        detail_storage=detail_storage,
        agent_subentry_id="agent",
        request_retention_days=request_retention_days,
        run_retention_days=run_retention_days,
    )


async def test_prune_applies_independent_retention_windows_and_persists_survivors() -> None:
    """Request and run detail retention are evaluated independently."""
    details = DetailStorage()
    manager = _manager(
        details,
        request_retention_days=10,
        run_retention_days=30,
    )
    now = dt_util.utcnow()
    manager.requests = [
        _request("recent", (now - timedelta(days=2)).isoformat()),
        _request("expired", (now - timedelta(days=20)).isoformat()),
    ]
    manager.runs = [
        _run("recent-run", (now - timedelta(days=20)).isoformat()),
        _run("expired-run", (now - timedelta(days=40)).isoformat()),
    ]

    result = await manager.async_prune_details()

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert [request.request_id for request in manager.requests] == ["recent"]
    assert [run.run_id for run in manager.runs] == ["recent-run"]
    assert details.data is not None
    assert [item["request_id"] for item in details.data["requests"]] == ["recent"]
    assert [item["run_id"] for item in details.data["runs"]] == ["recent-run"]


async def test_zero_retention_prunes_every_detail_without_saving_when_disabled() -> None:
    """Zero retention means keep no details, while save=False remains purely in-memory."""
    details = DetailStorage()
    manager = _manager(details, request_retention_days=0, run_retention_days=0)
    now = dt_util.utcnow().isoformat()
    manager.requests = [_request("request", now)]
    manager.runs = [_run("run", now)]

    result = await manager.async_prune_details(save=False)

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []
    assert details.saves == []
    assert details.data is None


async def test_clear_without_confirmation_is_non_destructive() -> None:
    """The destructive detail clear cannot proceed without explicit confirmation."""
    details = DetailStorage()
    manager = _manager(details)
    now = dt_util.utcnow().isoformat()
    request = _request("request", now)
    run = _run("run", now)
    manager.requests = [request]
    manager.runs = [run]

    with pytest.raises(ValueError, match="Explicit confirmation is required"):
        await manager.async_clear_details(confirm=False)

    assert manager.requests == [request]
    assert manager.runs == [run]
    assert details.saves == []


async def test_clear_without_detail_store_still_reports_and_clears_live_state() -> None:
    """Managers without detail persistence still support an explicit live clear."""
    manager = _manager(None)
    now = dt_util.utcnow().isoformat()
    manager.requests = [_request("first", now), _request("second", now)]
    manager.runs = [_run("run", now)]

    result = await manager.async_clear_details(confirm=True)

    assert result == {"deleted_requests": 2, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []


async def test_clear_save_failure_preserves_live_state_and_can_converge() -> None:
    """A failed durable clear stays unpublished, and a later retry can converge."""
    details = DetailStorage()
    manager = _manager(details)
    now = dt_util.utcnow().isoformat()
    request = _request("request", now)
    run = _run("run", now)
    manager.requests = [request]
    manager.runs = [run]
    details.fail_save = True

    with pytest.raises(OSError, match="detail store unavailable"):
        await manager.async_clear_details(confirm=True)

    # Durable-state hardening persists the candidate clear before publishing it.
    assert manager.requests == [request]
    assert manager.runs == [run]
    assert details.data is None

    details.fail_save = False
    result = await manager.async_clear_details(confirm=True)

    assert result == {"deleted_requests": 1, "deleted_runs": 1}
    assert manager.requests == []
    assert manager.runs == []
    assert details.data == {"requests": [], "runs": []}



class DelayedStorage:
    """Store stand-in exposing Home Assistant coalesced-save behavior."""

    def __init__(self) -> None:
        self.data = None
        self.immediate_saves = 0
        self.delayed: list[tuple[object, float]] = []

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        self.immediate_saves += 1
        self.data = deepcopy(data)

    def async_delay_save(self, data_func, delay: float = 0) -> None:
        self.delayed.append((data_func, delay))


async def test_routine_usage_persistence_is_owned_and_coalesced_by_manager() -> None:
    """Routine accounting remains off-path without lifecycle patch installation."""
    totals = DelayedStorage()
    daily = DelayedStorage()
    details = DelayedStorage()
    manager = UsageManager(totals, daily, details, agent_subentry_id="agent")
    await manager.async_initialize()

    async with manager.async_run(home_assistant_conversation_id="conversation"):
        await manager.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            provider="openai",
            model="gpt-test",
            api_mode="responses",
        )

    assert totals.immediate_saves == 0
    assert daily.immediate_saves == 0
    assert details.immediate_saves == 0
    assert totals.delayed and daily.delayed and details.delayed

    # Aggregate callbacks retain the point-in-time snapshot they were scheduled for.
    assert totals.delayed[-1][0]()["total_tokens"] == 6

    # Detail serialization remains lazy: the delayed callback reads the latest
    # coherent retained state rather than serializing history on the request path.
    manager.requests.clear()
    detail_payload = details.delayed[-1][0]()
    assert detail_payload["requests"] == []
    assert len(detail_payload["runs"]) == 1


async def test_usage_schedule_failure_falls_back_to_immediate_save(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken delayed-save boundary falls back to the supplied save coroutine."""

    class FailingDelayedStorage(DetailStorage):
        def async_delay_save(self, _data_func, _delay: float = 0) -> None:
            raise RuntimeError("scheduler unavailable")

    details = FailingDelayedStorage()
    manager = UsageManager(TotalsStorage(), detail_storage=details)
    calls = 0

    async def save() -> None:
        nonlocal calls
        calls += 1

    with caplog.at_level(logging.ERROR):
        await manager._async_save_safely("request details", save)

    assert calls == 1
    assert "falling back to immediate persistence" in caplog.text


def test_usage_snapshot_rejects_unknown_category() -> None:
    manager = _manager(None)
    with pytest.raises(ValueError, match="Unknown usage persistence category"):
        manager._usage_snapshot("unknown")


async def test_usage_retention_scheduler_is_off_path_and_deduplicated() -> None:
    """Daily retention starts one named background task and avoids duplicate work."""
    manager = _manager(None)
    calls: list[bool] = []
    release = asyncio.Event()

    async def prune(*, save: bool = True):
        calls.append(save)
        await release.wait()
        manager._last_prune_date = dt_util.utcnow().date().isoformat()

    manager.async_prune_details = prune  # type: ignore[method-assign]

    await manager._async_prune_usage_if_due()
    task = manager._prune_task
    assert isinstance(task, asyncio.Task)
    assert task.get_name() == "extended_openai_usage_retention_maintenance"

    await manager._async_prune_usage_if_due()
    assert manager._prune_task is task
    assert calls == []

    await asyncio.sleep(0)
    assert calls == [True]
    release.set()
    await task
    await asyncio.sleep(0)
    assert manager._prune_task is None

    await manager._async_prune_usage_if_due()
    assert manager._prune_task is None
    assert calls == [True]


async def test_usage_retention_scheduler_bounds_same_day_retry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed background prune retries only after cooldown and at most twice."""
    manager = _manager(None)
    now = 200.0
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: now)
    monkeypatch.setattr(usage_module, "_USAGE_PRUNE_RETRY_SECONDS", 30.0)
    attempts = 0

    async def fail(*, save: bool = True):
        nonlocal attempts
        assert save is True
        attempts += 1
        raise OSError("detail storage unavailable")

    manager.async_prune_details = fail  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await manager._async_prune_usage_if_due()
        first = manager._prune_task
        assert first is not None
        await first
        await asyncio.sleep(0)

    assert attempts == 1
    assert manager._next_prune_retry == 230.0
    assert "Background usage retention maintenance failed" in caplog.text

    await manager._async_prune_usage_if_due()
    assert attempts == 1

    now = 231.0
    await manager._async_prune_usage_if_due()
    second = manager._prune_task
    assert second is not None
    await second
    await asyncio.sleep(0)
    assert attempts == 2

    now = 1000.0
    await manager._async_prune_usage_if_due()
    assert manager._prune_task is None
    assert attempts == 2
