"""Admission limits reject whole requests before allocating or persisting work."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    delayed_tools as delayed,
    intercom,
)
from homeassistant.exceptions import HomeAssistantError


def broadcast(hass, monkeypatch):
    manager = intercom.IntercomManager(hass)
    manager._enabled = True
    manager.resolve_targets = Mock(return_value=["assist_satellite.one"])
    manager._schedule_drain = Mock()
    monkeypatch.setattr(intercom, "async_call_later", lambda *args: Mock())
    hass.states.get.return_value = SimpleNamespace(state="responding")
    return manager


async def test_broadcast_length_and_target_limits_have_no_side_effects(
    hass, monkeypatch
):
    manager = broadcast(hass, monkeypatch)
    with pytest.raises(HomeAssistantError, match="characters"):
        await manager.async_send(" " * (intercom.MAX_BROADCAST_MESSAGE_CHARACTERS + 1))
    manager.resolve_targets.return_value = [
        f"assist_satellite.s{i}" for i in range(intercom.MAX_BROADCAST_TARGETS + 1)
    ]
    with pytest.raises(HomeAssistantError, match="target limit"):
        await manager.async_send("hello")
    assert manager.history() == []
    assert not manager._queues and not manager._expiry_unsubscribers
    manager._schedule_drain.assert_not_called()


async def test_broadcast_rejects_multi_target_send_atomically_and_reuses_expired_capacity(
    hass, monkeypatch
):
    monkeypatch.setattr(intercom, "MAX_BROADCAST_QUEUE_PER_TARGET", 1)
    manager = broadcast(hass, monkeypatch)
    first = await manager.async_send("one")
    manager.resolve_targets.return_value = [
        "assist_satellite.two",
        "assist_satellite.one",
    ]
    with pytest.raises(HomeAssistantError, match="queue capacity"):
        await manager.async_send("two")
    assert "assist_satellite.two" not in manager._queues
    assert len(manager.history()) == len(manager._expiry_unsubscribers) == 1
    manager._expire(first["id"])
    await manager.async_send("two")
    assert len(manager._expiry_unsubscribers) == 1
    await manager.async_shutdown()
    assert (
        not manager._queues
        and not manager._expiry_unsubscribers
        and not manager._expiry_by_message
    )


@pytest.mark.parametrize(
    "constant", ["MAX_BROADCAST_MESSAGES", "MAX_BROADCAST_DELIVERIES"]
)
async def test_broadcast_global_capacity(hass, monkeypatch, constant):
    monkeypatch.setattr(intercom, constant, 1)
    manager = broadcast(hass, monkeypatch)
    await manager.async_send("one")
    manager.resolve_targets.return_value = ["assist_satellite.two"]
    with pytest.raises(HomeAssistantError, match="capacity"):
        await manager.async_send("two")
    assert len(manager.history()) == 1


async def test_completed_broadcast_releases_expiry_timer(hass, monkeypatch):
    monkeypatch.setattr(intercom, "IDLE_STABILITY_SECONDS", 0)
    manager = broadcast(hass, monkeypatch)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    await manager.async_send("one")
    await manager._async_drain("assist_satellite.one")
    assert not manager._expiry_unsubscribers and not manager._expiry_by_message


def scheduler(hass):
    manager = delayed.DelayedToolManager(hass)
    manager._setup_complete = True
    manager._store = SimpleNamespace(async_save=AsyncMock())
    return manager


def agent(name="one"):
    return SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id=name),
    )


async def test_delayed_per_agent_capacity_is_atomic_under_concurrent_sends(
    hass, monkeypatch
):
    monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS_PER_AGENT", 2)
    manager = scheduler(hass)
    results = await asyncio.gather(
        *(
            manager.async_schedule(agent(), "tool", {"delay": 60}, None)
            for _ in range(3)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, HomeAssistantError) for result in results) == 1
    assert len(manager._records) == manager._store.async_save.await_count == 2
    assert not manager._tasks
    await manager.async_remove_agent("entry", "one")
    await manager.async_schedule(agent(), "tool", {"delay": 60}, None)
    assert len(manager._records) == 1


async def test_delayed_global_and_argument_limits_reject_before_save(hass, monkeypatch):
    manager = scheduler(hass)
    with pytest.raises(HomeAssistantError, match="arguments exceed"):
        await manager.async_schedule(
            agent(),
            "tool",
            {"delay": 60, "text": "x" * delayed.MAX_DELAYED_ARGUMENT_BYTES},
            None,
        )
    manager._store.async_save.assert_not_called()
    monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS", 1)
    await manager.async_schedule(agent(), "tool", {"delay": 60}, None)
    with pytest.raises(HomeAssistantError, match="capacity"):
        await manager.async_schedule(agent("two"), "tool", {"delay": 60}, None)
    assert manager._store.async_save.await_count == 1


async def test_oversized_recovered_queue_is_preserved_and_not_armed(hass, monkeypatch):
    existing = scheduler(hass)
    record = await existing.async_schedule(agent(), "tool", {"delay": 60}, None)
    rows = [replace(record, call_id=str(index)).as_dict() for index in range(3)]
    manager = delayed.DelayedToolManager(hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"calls": rows}), async_save=AsyncMock()
    )
    monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS", 2)
    with pytest.raises(HomeAssistantError, match="capacity"):
        await manager._async_load_locked()
    manager._store.async_save.assert_not_called()
    assert not manager._setup_complete and not manager._tasks


async def test_failed_save_preserves_capacity_for_live_executing_calls(
    hass, monkeypatch
):
    manager = scheduler(hass)
    record = await manager.async_schedule(agent(), "tool", {"delay": 60}, None)
    executing = replace(record, status="executing")
    manager._records[record.call_id] = executing
    worker = asyncio.create_task(asyncio.Event().wait())
    manager._tasks[record.call_id] = worker
    manager._store.async_load = AsyncMock(return_value={"calls": [executing.as_dict()]})
    monkeypatch.setattr(delayed, "MAX_DELAYED_CALLS", 1)
    try:
        await manager._async_reconcile_failed_save()
        with pytest.raises(HomeAssistantError, match="capacity"):
            await manager.async_schedule(agent("two"), "tool", {"delay": 60}, None)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
