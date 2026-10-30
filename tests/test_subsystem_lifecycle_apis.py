"""Event-specific lifecycle APIs preserve disposal and restore boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    continuity,
    temporary_memory,
    usage,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.resource_lifecycle_ledger import assert_owned_removed, capture_owned


def _seeded_continuity(**state):
    """Use the real reset API in coordinator tests with independently seeded state."""
    manager = continuity.ConversationContinuity("test")
    vars(manager).update(state)
    return manager


async def _pending_task():
    started = asyncio.Event()
    finished = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    task = asyncio.create_task(work())
    await started.wait()
    return task, finished


@pytest.mark.parametrize("state", ["pending", "finished", "absent"])
@pytest.mark.parametrize("kind", ["usage", "temporary"])
async def test_deletion_preparation_drains_work_without_persistence(
    kind, state, tmp_path
):
    store = Mock(async_save=AsyncMock(), async_load=AsyncMock(return_value=None))
    if kind == "usage":
        manager = usage.UsageManager(store, store, store)
        field = "_prune_task"
        cancel_timer, cancel_stop = Mock(), Mock()
        manager._cancel_retention = cancel_timer
        manager._cancel_stop = cancel_stop
        manager.totals.api_request_count = 7
    else:
        manager = temporary_memory.TemporaryMemory(store)
        field = "_prune_save_task"
        manager.expired_pruned = 7
    task, finished = await _pending_task()
    if state != "pending":
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    setattr(manager, field, None if state == "absent" else task)

    registry = {("entry", "agent"): manager}
    hass = SimpleNamespace(
        data={f"{DOMAIN}.{kind}_managers": registry},
        config=SimpleNamespace(path=lambda _: str(tmp_path)),
    )
    before = capture_owned(hass, "entry", "agent")

    await manager.async_prepare_for_deletion()
    await manager.async_prepare_for_deletion()
    registry.clear()
    assert_owned_removed(before, capture_owned(hass, "entry", "agent"))

    assert finished.is_set()
    assert task.cancelled()
    store.async_save.assert_not_called()
    if kind == "usage":
        assert manager._stopping
        assert manager.totals.api_request_count == 7
        cancel_timer.assert_called_once()
        cancel_stop.assert_called_once()
    else:
        assert manager.expired_pruned == 7


async def test_usage_normal_shutdown_still_flushes():
    stores = [
        Mock(async_save=AsyncMock(), async_load=AsyncMock(return_value=None))
        for _ in range(3)
    ]
    manager = usage.UsageManager(*stores)
    await manager.async_initialize()
    await manager.async_shutdown()
    for store in stores:
        store.async_save.assert_awaited_once()


@pytest.mark.parametrize("kind", ["usage", "temporary"])
async def test_prune_finalizer_error_does_not_prevent_deletion_preparation(kind):
    store = Mock(async_save=AsyncMock())
    manager = (
        usage.UsageManager(store)
        if kind == "usage"
        else temporary_memory.TemporaryMemory(store)
    )
    started = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise OSError("failed prune finalizer")

    task = asyncio.create_task(work())
    await started.wait()
    setattr(manager, "_prune_task" if kind == "usage" else "_prune_save_task", task)
    await manager.async_prepare_for_deletion()
    assert task.done()
    assert isinstance(task.exception(), OSError)
    store.async_save.assert_not_called()
    if kind == "usage":
        assert manager._stopping


def test_continuity_reset_keeps_diagnostics_and_lock_ownership():
    manager = _seeded_continuity(
        _sessions={"old": object()},
        _memory_bundles={"old": object()},
        _pending_ends={"old"},
        _ignored_conversation_ids={"old": None},
    )
    manager.resume_count = 3
    manager.new_session_count = 5
    lock = asyncio.Lock()
    manager._ha_default_locks["owner"] = lock
    manager._ha_default_lock_users["owner"] = 1
    manager._ha_default_claims["claim"] = ("owner", lock)
    manager.reset_all()
    manager.reset_all()
    assert not manager._sessions
    assert not manager._memory_bundles
    assert not manager._pending_ends
    assert not manager._ignored_conversation_ids
    assert (manager.resume_count, manager.new_session_count) == (3, 5)
    assert manager._ha_default_locks == {"owner": lock}
    assert manager._ha_default_lock_users == {"owner": 1}
    assert manager._ha_default_claims == {"claim": ("owner", lock)}


@pytest.mark.parametrize(
    "suffix", ["usage_managers", "volatile_usage_managers", "temporary_memory_managers"]
)
async def test_coordinator_calls_explicit_preparation_before_store_discovery(
    hass, monkeypatch, suffix
):
    key = ("entry", "agent")
    manager = Mock(async_prepare_for_deletion=AsyncMock())
    hass.data[f"{DOMAIN}.{suffix}"] = {key: manager}

    async def remove_stores(gate):
        assert gate is agent_deletion.get_agent_maintenance_gate(hass, *key)
        assert gate.owns_exclusive()
        manager.async_prepare_for_deletion.assert_awaited_once()

    monkeypatch.setattr(agent_deletion, "async_remove_agent_stores", remove_stores)
    monkeypatch.setattr(agent_deletion, "_storage_names", lambda *_: [])
    await agent_deletion.async_delete_agent_data(hass, *key)
    assert key not in hass.data[f"{DOMAIN}.{suffix}"]
