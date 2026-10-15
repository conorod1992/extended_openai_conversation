"""Lifecycle APIs operate on loaded HA managers and real private store files."""

import asyncio
from pathlib import Path

import pytest

from homeassistant.helpers.storage import Store

from custom_components.extended_openai_conversation_responses import (
    agent_deletion,
    backup,
    temporary_memory,
    usage,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests_real_ha.test_live_subentry_removal import (
    _entry,
    _setup_entry,
    _subentry_by_title,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


async def _pending_task():
    started = asyncio.Event()

    async def work():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await started.wait()
    return task


@pytest.mark.parametrize("fail_first_remove", [False, True])
async def test_loaded_deletion_drains_pruning_without_flush_and_can_retry(
    hass, real_store_io, monkeypatch, fail_first_remove
):
    entry = _entry()
    await _setup_entry(hass, entry)
    owner = _subentry_by_title(entry, "Conversation A")
    sibling = _subentry_by_title(entry, "Conversation B")
    key = (entry.entry_id, owner.subentry_id)
    manager = await usage.async_get_usage(hass, *key)
    temporary = await temporary_memory.async_get_temporary_memory(hass, *key)
    sibling_usage = await usage.async_get_usage(
        hass, entry.entry_id, sibling.subentry_id
    )
    assert manager._cancel_stop is not None
    assert manager._cancel_retention is not None
    # This inventory is independent of production discovery and manager handles.
    names = [
        f"{DOMAIN}.usage.{entry.entry_id}.{owner.subentry_id}{suffix}"
        for suffix in ("", ".daily", ".details")
    ] + [f"{DOMAIN}.temporary_memory.{entry.entry_id}.{owner.subentry_id}"]
    paths = []
    for name in names:
        store = Store(hass, 1, name)
        await store.async_save({"private": "erase me"})
        paths.append(Path(store.path))
    sibling_store = Store(
        hass, 1, f"{DOMAIN}.usage.{entry.entry_id}.{sibling.subentry_id}.sentinel"
    )
    await sibling_store.async_save({"private": "retain sibling"})
    manager._prune_task = usage_task = await _pending_task()
    temporary._prune_save_task = temporary_task = await _pending_task()
    gate = agent_deletion.get_agent_maintenance_gate(hass, *key)
    original_remove = Store.async_remove
    original_save = Store.async_save
    writes = []

    async def observe_save(store, data):
        if Path(store.path) in paths:
            writes.append(store.key)
        return await original_save(store, data)

    async def fail_remove(store):
        if Path(store.path) == paths[0]:
            raise OSError("retryable erasure failure")
        await original_remove(store)

    monkeypatch.setattr(Store, "async_save", observe_save)
    if fail_first_remove:
        with monkeypatch.context() as scoped:
            scoped.setattr(Store, "async_remove", fail_remove)
            with pytest.raises(OSError, match="retryable"):
                await agent_deletion.async_delete_agent_data(hass, *key)
        assert not gate.deleted
        assert key in hass.data[usage._USAGE_MANAGERS]
        assert usage_task.cancelled() and temporary_task.cancelled()

    await agent_deletion.async_delete_agent_data(hass, *key)
    await agent_deletion.async_delete_agent_data(hass, *key)
    assert usage_task.cancelled() and temporary_task.cancelled()
    assert manager._stopping
    assert manager._cancel_retention is None
    assert manager._cancel_stop is None
    assert not writes
    assert all(not path.exists() for path in paths)
    assert Path(sibling_store.path).exists()
    assert not sibling_usage._stopping
    assert sibling_usage._cancel_stop is not None
    assert key not in hass.data[usage._USAGE_MANAGERS]
    assert key not in hass.data[temporary_memory._MANAGERS]
    assert gate.deleted
    # A later normal shutdown must honor the deletion fence, not recreate files.
    await manager.async_shutdown()
    assert all(not path.exists() for path in paths)


async def test_loaded_restore_resets_continuity_without_replacing_live_owner(hass):
    from homeassistant.components import conversation

    entry = _entry()
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    manager = agent._continuity
    snapshot = await backup.async_collect_backup_snapshot(hass, entry, agent.subentry)
    await manager.async_set_memory_bundle("cached", [("id", "stale")], 5)
    await manager.async_ignore_next_incoming_conversation_id("stale-chat")
    manager.resume_count = 4
    manager.new_session_count = 6
    assert await manager.async_get_memory_bundle("cached", 5)

    result = await backup.async_restore_backup(hass, entry, agent.subentry, snapshot)
    assert result["status"] == "restored"
    assert agent._continuity is manager
    assert await manager.async_get_memory_bundle("cached", 5) is None
    assert not manager._ignored_conversation_ids
    assert (manager.resume_count, manager.new_session_count) == (4, 6)
