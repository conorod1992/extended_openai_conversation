"""Retention scheduler coverage for durable state hardening."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    durable_state_hardening as hardening,
    lifecycle_optimizations as lifecycle,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager


@pytest.fixture
def usage_transactions(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install fresh Usage wrappers and restore both patched boundaries afterward."""
    original_prune = UsageManager.async_prune_details
    original_clear = UsageManager.async_clear_details
    original_scheduler = lifecycle._async_prune_usage_if_due
    monkeypatch.setattr(UsageManager, "async_prune_details", original_prune)
    monkeypatch.setattr(UsageManager, "async_clear_details", original_clear)
    monkeypatch.setattr(lifecycle, "_async_prune_usage_if_due", original_scheduler)
    monkeypatch.setattr(
        original_prune, "_extended_openai_persist_first", False, raising=False
    )

    hardening._install_usage_transactions()
    return lifecycle._async_prune_usage_if_due


@pytest.mark.asyncio
async def test_usage_scheduler_skips_completed_retry_limited_and_running_work(
    usage_transactions: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = usage_transactions
    today = hardening.dt_util.utcnow().date().isoformat()
    now = 1000.0
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: now)

    calls = 0

    async def prune(*, save: bool = True) -> None:
        nonlocal calls
        assert save is True
        calls += 1

    completed = SimpleNamespace(async_prune_details=prune)
    setattr(completed, lifecycle._LAST_USAGE_PRUNE_DATE, today)
    await scheduler(completed)

    retrying = SimpleNamespace(async_prune_details=prune)
    setattr(retrying, hardening._USAGE_PRUNE_ATTEMPT_DATE, today)
    setattr(retrying, lifecycle._NEXT_USAGE_PRUNE_RETRY, now + 10)
    await scheduler(retrying)

    exhausted = SimpleNamespace(async_prune_details=prune)
    setattr(exhausted, hardening._USAGE_PRUNE_ATTEMPT_DATE, today)
    setattr(
        exhausted,
        hardening._USAGE_PRUNE_ATTEMPT_COUNT,
        hardening._USAGE_PRUNE_MAX_ATTEMPTS_PER_DAY,
    )
    setattr(exhausted, lifecycle._NEXT_USAGE_PRUNE_RETRY, 0.0)
    await scheduler(exhausted)

    blocker = asyncio.create_task(asyncio.sleep(60))
    running = SimpleNamespace(async_prune_details=prune)
    setattr(running, hardening._USAGE_PRUNE_TASK, blocker)
    try:
        await scheduler(running)
    finally:
        blocker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocker

    assert calls == 0


@pytest.mark.asyncio
async def test_usage_scheduler_runs_once_and_done_callback_clears_task(
    usage_transactions: Any,
) -> None:
    scheduler = usage_transactions
    calls: list[bool] = []

    async def prune(*, save: bool = True) -> None:
        calls.append(save)

    manager = SimpleNamespace(async_prune_details=prune)

    await scheduler(manager)
    task = getattr(manager, hardening._USAGE_PRUNE_TASK)
    assert isinstance(task, asyncio.Task)
    await task
    await asyncio.sleep(0)

    assert calls == [True]
    assert getattr(manager, hardening._USAGE_PRUNE_ATTEMPT_COUNT) == 1
    assert getattr(manager, hardening._USAGE_PRUNE_TASK) is None


@pytest.mark.asyncio
async def test_usage_scheduler_failure_sets_bounded_retry(
    usage_transactions: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scheduler = usage_transactions
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(lifecycle, "_USAGE_PRUNE_RETRY_SECONDS", 30.0)

    async def prune(*, save: bool = True) -> None:
        assert save is True
        raise RuntimeError("storage unavailable")

    manager = SimpleNamespace(async_prune_details=prune)

    await scheduler(manager)
    task = getattr(manager, hardening._USAGE_PRUNE_TASK)
    await task
    await asyncio.sleep(0)

    assert getattr(manager, lifecycle._NEXT_USAGE_PRUNE_RETRY) == 230.0
    assert "Background usage retention maintenance failed" in caplog.text


@pytest.mark.asyncio
async def test_usage_transaction_methods_persist_before_publishing(
    usage_transactions: Any,
) -> None:
    events: list[Any] = []

    class Storage:
        async def async_save(self, data: dict[str, Any]) -> None:
            events.append(("save", data))

    manager = SimpleNamespace(
        _lock=asyncio.Lock(),
        _detail_storage=Storage(),
        requests=[],
        runs=[],
        request_retention_days=30,
        run_retention_days=30,
    )

    result = await UsageManager.async_prune_details(manager, save=True)
    assert result == {"deleted_requests": 0, "deleted_runs": 0}
    assert events == [("save", {"requests": [], "runs": []})]
    assert getattr(manager, lifecycle._LAST_USAGE_PRUNE_DATE)
    assert getattr(manager, lifecycle._NEXT_USAGE_PRUNE_RETRY) == 0.0

    with pytest.raises(ValueError, match="Explicit confirmation"):
        await UsageManager.async_clear_details(manager, confirm=False)

    result = await UsageManager.async_clear_details(manager, confirm=True)
    assert result == {"deleted_requests": 0, "deleted_runs": 0}
    assert events[-1] == ("save", {"requests": [], "runs": []})


@pytest.mark.asyncio
async def test_archive_retention_schedule_registers_daily_callback_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    scheduled: list[Any] = []
    removals: list[Any] = []

    async def original_added(agent: Any) -> None:
        calls.append(("original", agent))

    async def retention(agent: Any, now: Any = None) -> None:
        calls.append(("retention", agent, now))

    def track(hass: Any, callback: Any, interval: Any) -> str:
        scheduled.append((hass, callback, interval))
        return "unsubscribe"

    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "async_added_to_hass", original_added
    )
    monkeypatch.setattr(hardening, "async_track_time_interval", track)
    monkeypatch.setattr(hardening, "async_prune_archive_retention", retention)

    hardening._install_archive_retention_schedule()
    installed = ExtendedOpenAIAgentEntity.async_added_to_hass
    hardening._install_archive_retention_schedule()
    assert ExtendedOpenAIAgentEntity.async_added_to_hass is installed

    agent = SimpleNamespace(
        hass="hass",
        async_on_remove=lambda value: removals.append(value),
    )
    await installed(agent)

    assert calls == [("original", agent)]
    assert removals == ["unsubscribe"]
    assert len(scheduled) == 1
    assert scheduled[0][0] == "hass"
    assert scheduled[0][2] == hardening._ARCHIVE_RETENTION_INTERVAL

    marker = object()
    await scheduled[0][1](marker)
    assert calls[-1] == ("retention", agent, marker)
