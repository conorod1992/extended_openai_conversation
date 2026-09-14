"""Focused lifecycle and race coverage for durable delayed Function Tools."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolCall,
    DelayedToolManager,
    _AGENT_RETRY_SECONDS,
)


def _record(*, due_at: str | None = None) -> DelayedToolCall:
    now = dt_util.utcnow()
    return DelayedToolCall(
        call_id="call-1",
        entry_id="entry",
        subentry_id="agent",
        tool_name="control_light",
        arguments={"delay": {"seconds": 5}, "value": 1},
        due_at=due_at or (now - timedelta(seconds=1)).isoformat(),
        created_at=(now - timedelta(seconds=6)).isoformat(),
        user_id="user-1",
        device_id="device-1",
    )


async def test_concurrent_setup_loads_and_registers_once(hass) -> None:
    """A second setup waiter must observe completion instead of loading twice."""
    manager = DelayedToolManager(hass)
    load_started = asyncio.Event()
    release_load = asyncio.Event()
    load_count = 0

    async def async_load():
        nonlocal load_count
        load_count += 1
        load_started.set()
        await release_load.wait()
        return {"calls": []}

    manager._store = SimpleNamespace(
        async_load=async_load,
        async_save=AsyncMock(),
    )

    first = asyncio.create_task(manager.async_setup())
    await load_started.wait()
    second = asyncio.create_task(manager.async_setup())
    await asyncio.sleep(0)

    release_load.set()
    await asyncio.gather(first, second)

    assert load_count == 1
    assert manager._setup_complete is True


async def test_arm_does_not_duplicate_an_active_waiter(hass, monkeypatch) -> None:
    """Repeated arming of one call must not create duplicate execution waiters."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    started = asyncio.Event()
    release = asyncio.Event()
    invocations = 0

    async def waiter(call_id: str) -> None:
        nonlocal invocations
        assert call_id == record.call_id
        invocations += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(manager, "_async_wait_and_execute", waiter)

    manager._arm(record.call_id)
    first_task = manager._tasks[record.call_id]
    await started.wait()

    manager._arm(record.call_id)

    assert manager._tasks[record.call_id] is first_task
    assert invocations == 1

    release.set()
    await first_task
    manager._tasks.pop(record.call_id, None)


async def test_waiter_does_not_execute_record_removed_while_waiting(
    hass, monkeypatch
) -> None:
    """A call removed during its due-time wait must never execute afterward."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._started = True
    execute_due = AsyncMock()
    monkeypatch.setattr(manager, "_async_execute_due", execute_due)

    async def remove_during_sleep(_delay: float) -> None:
        manager._records.pop(record.call_id, None)

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.asyncio.sleep",
        remove_during_sleep,
    )

    await manager._async_wait_and_execute(record.call_id)

    execute_due.assert_not_awaited()
    assert record.call_id not in manager._tasks


async def test_invalid_due_timestamp_retries_when_discard_cannot_persist(
    hass, monkeypatch
) -> None:
    """A failed discard is retried rather than executing a malformed persisted call."""
    manager = DelayedToolManager(hass)
    record = _record(due_at="not-a-timestamp")
    manager._records = {record.call_id: record}
    manager._started = True
    discard = AsyncMock(side_effect=[False, True])
    execute_due = AsyncMock()
    monkeypatch.setattr(manager, "_async_discard", discard)
    monkeypatch.setattr(manager, "_async_execute_due", execute_due)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.asyncio.sleep",
        fake_sleep,
    )

    await manager._async_wait_and_execute(record.call_id)

    assert discard.await_count == 2
    assert sleeps == [_AGENT_RETRY_SECONDS]
    execute_due.assert_not_awaited()


async def test_transient_execution_failure_retries_same_pending_call(
    hass, monkeypatch
) -> None:
    """A retryable due-call failure must loop instead of abandoning the call."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._started = True
    execute_due = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(manager, "_async_execute_due", execute_due)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    class Gate:
        def shared(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.asyncio.sleep",
        fake_sleep,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.get_agent_maintenance_gate",
        lambda *_args: Gate(),
    )

    await manager._async_wait_and_execute(record.call_id)

    assert execute_due.await_count == 2
    assert _AGENT_RETRY_SECONDS in sleeps


async def test_waiter_cancellation_propagates_and_removes_task(hass, monkeypatch) -> None:
    """Stopping a pending waiter must not leave a stale task registration behind."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._started = True
    sleeping = asyncio.Event()
    never_release = asyncio.Event()

    async def blocking_sleep(_delay: float) -> None:
        sleeping.set()
        await never_release.wait()

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.asyncio.sleep",
        blocking_sleep,
    )

    task = asyncio.create_task(manager._async_wait_and_execute(record.call_id))
    manager._tasks[record.call_id] = task
    await sleeping.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert record.call_id not in manager._tasks
