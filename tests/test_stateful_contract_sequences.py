"""Short deterministic sequences and explicitly controlled maintenance races."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    RecoveryGuardedStore,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryStore,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from tests.memory_sequence_contract import run_memory_sequence
from tests.test_memory import FakeStorage


@pytest.mark.parametrize("seed", [7, 1060, 4294967295])
async def test_memory_provenance_pagination_and_reload_sequence(seed):
    storage = FakeStorage()
    await run_memory_sequence(lambda: storage, seed=seed, steps=40, trace=[])


@pytest.mark.parametrize("subsystem", ["temporary_memory", "usage"])
@pytest.mark.parametrize("maintenance", ["restore", "delete", "cancel_restore"])
async def test_background_pruning_and_maintenance_have_bounded_settlement(
    hass, monkeypatch, subsystem, maintenance
):
    gate = get_agent_maintenance_gate(hass, "entry", "agent")
    sibling = get_agent_maintenance_gate(hass, "entry", "sibling")
    writes = []

    async def load(store):
        return None

    async def save(store, value):
        writes.append(deepcopy(value))

    # Keep the real guarded Store, manager locking and transactional save path.
    # Replace only HA's native disk seam in this fast deterministic test.
    monkeypatch.setattr(Store, "async_load", load)
    monkeypatch.setattr(Store, "async_save", save)
    if subsystem == "temporary_memory":
        store = TemporaryMemoryStore(hass, 1, "race").bind_agent("entry", "agent")
        manager = TemporaryMemory(store)
        await manager.async_initialize()
    else:
        store = RecoveryGuardedStore(hass, 1, "race").bind_agent("entry", "agent")
        manager = UsageManager(store, detail_storage=store)
    admitted = asyncio.Event()
    attempted = asyncio.Event()
    original_shared = gate.shared
    original_exclusive = gate.exclusive

    @asynccontextmanager
    async def observed_shared(**kwargs):
        async with original_shared(**kwargs):
            admitted.set()
            yield

    @asynccontextmanager
    async def observed_exclusive():
        attempted.set()
        async with original_exclusive():
            yield

    monkeypatch.setattr(gate, "shared", observed_shared)
    monkeypatch.setattr(gate, "exclusive", observed_exclusive)
    await manager._lock.acquire()
    tasks = []
    try:
        if subsystem == "temporary_memory":
            manager._schedule_pruned_state_save()
            prune = manager._prune_save_task
        else:
            await manager._async_prune_usage_if_due()
            prune = manager._prune_task
        tasks.append(prune)
        await asyncio.wait_for(admitted.wait(), 2)

        async def maintain():
            async with gate.exclusive(), manager._lock:
                assert writes, (
                    "Pruning must commit before maintenance takes its manager lock"
                )
                if maintenance == "delete":
                    gate.deleted = True

        writer = asyncio.create_task(maintain())
        tasks.append(writer)
        await asyncio.wait_for(attempted.wait(), 2)
        async with sibling.shared():
            assert sibling._active_readers == 1
        if maintenance == "cancel_restore":
            writer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writer
        manager._lock.release()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert prune.exception() is None
        if maintenance != "cancel_restore":
            assert writer.exception() is None
        assert writes
        assert gate._active_readers == gate._waiting_writers == 0
        assert not gate._writer_active
        if maintenance == "delete":
            with pytest.raises(HomeAssistantError, match="deleted"):
                async with gate.shared():
                    pytest.fail("Deleted generation admitted a late writer")
        else:
            async with gate.shared():
                pass
    finally:
        if manager._lock.locked():
            manager._lock.release()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("changed", [False, True])
async def test_delayed_retry_reload_and_definition_change_sequence(
    hass, monkeypatch, changed
):
    from types import SimpleNamespace

    from custom_components.extended_openai_conversation_responses import (
        delayed_tools as owner,
    )
    from tests.test_delayed_tools import _coverage_record, _live_entry, _valid_tool
    from tests.test_memory import FakeStorage

    storage = FakeStorage()
    manager = owner.DelayedToolManager(hass)
    manager._store = storage
    record = _coverage_record(user_id=None)
    manager._records = {record.call_id: record}
    hass.config_entries.async_get_entry.return_value = _live_entry()
    tool = _valid_tool()
    monkeypatch.setattr(
        owner, "configured_function_tools_from_data", lambda data: [tool]
    )
    monkeypatch.setattr(manager, "_resolve_agent", lambda *args: None)
    assert await manager._async_execute_due(record.call_id) is True
    assert manager._records[record.call_id].retry_count == 1
    persisted = storage.data["calls"][0]
    restarted = owner.DelayedToolManager(hass)
    restarted._store = storage
    recovered = owner.DelayedToolCall.from_dict(persisted)
    restarted._records = {recovered.call_id: recovered}
    # Editing the same name must not silently replace the authorized action.
    if changed:
        tool["function"]["name"] = "different_implementation"
    agent = SimpleNamespace(_execute_function_tool=AsyncMock(return_value=object()))
    monkeypatch.setattr(restarted, "_resolve_agent", lambda *args: agent)
    monkeypatch.setattr(owner, "get_exposed_entities", lambda hass: [])
    assert await restarted._async_execute_due(record.call_id) is False
    assert agent._execute_function_tool.await_count == int(not changed)
    assert not restarted._records
    assert storage.data["calls"] == []


@pytest.mark.parametrize("fix", ["disable", "change", "delete"])
async def test_rule_repair_tracks_committed_changes_not_failed_writes(
    hass, monkeypatch, fix
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from custom_components.extended_openai_conversation_responses import (
        model_lifecycle,
        request_rules as rr,
    )
    from tests.test_request_rules import MemoryStore, routing_rule

    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        title="Assistant",
        data={"chat_model": "gpt-5.6"},
    )
    entry = SimpleNamespace(entry_id="entry", subentries={"agent": subentry})
    storage = MemoryStore()
    rules = rr.RequestRules(
        storage,
        on_change=lambda: model_lifecycle.sync_entry_model_lifecycle(hass, entry),
    )
    hass.data[rr._MANAGERS] = {("entry", "agent"): rules}
    monkeypatch.setattr(
        model_lifecycle,
        "lifecycle_snapshot",
        lambda model, **kwargs: {
            "model": model,
            "status": "deprecated" if model == "gpt-5.1" else "current",
            "shutdown_reached": model == "gpt-5.1",
        },
    )
    monkeypatch.setattr(
        model_lifecycle.ir,
        "async_get",
        lambda hass: SimpleNamespace(async_get_issue=lambda *args: None),
    )
    delete = Mock()
    monkeypatch.setattr(model_lifecycle.ir, "async_delete_issue", delete)
    await rules.async_initialize()
    value = routing_rule(scope="conversation")
    value["action"]["model"] = "gpt-5.1"
    created = await rules.async_create(value)
    hass.data[model_lifecycle._DATA_FAILURES] = {
        ("entry", "agent"): {"configured_model": "gpt-5.6", "model": "gpt-5.1"}
    }
    model_lifecycle.sync_entry_model_lifecycle(hass, entry)
    delete.reset_mock()
    fixed = deepcopy(created)
    if fix == "disable":
        fixed["enabled"] = False
    elif fix == "change":
        fixed["action"]["model"] = "gpt-5.6"

    async def commit():
        if fix == "delete":
            return await rules.async_delete(created["id"])
        return await rules.async_update(created["id"], fixed)

    with monkeypatch.context() as outage:
        outage.setattr(
            storage, "async_save", AsyncMock(side_effect=OSError("controlled outage"))
        )
        with pytest.raises(OSError):
            await commit()
    assert ("entry", "agent") in hass.data[model_lifecycle._DATA_FAILURES]
    assert not delete.called
    await commit()
    assert ("entry", "agent") not in hass.data[model_lifecycle._DATA_FAILURES]
    assert delete.called
