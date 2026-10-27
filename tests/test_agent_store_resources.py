"""Agent storage ownership follows live handles, not manager object layouts."""

import asyncio
import gc
from threading import Barrier
from unittest.mock import AsyncMock, Mock
from weakref import ref

import pytest

from custom_components.extended_openai_conversation_responses import agent_maintenance
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantEmbeddingCacheStorage,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    RecoveryGuardedStore,
    async_remove_agent_stores,
)
from homeassistant.helpers.storage import Store


def test_gate_lookup_does_not_construct_discarded_owners(hass, monkeypatch):
    original = agent_maintenance.AgentMaintenanceGate
    created = []

    class CountedGate(original):
        def __init__(self):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(agent_maintenance, "AgentMaintenanceGate", CountedGate)
    owner = agent_maintenance.get_agent_maintenance_gate(hass, "provider", "agent")
    for _ in range(10):
        assert (
            agent_maintenance.get_agent_maintenance_gate(hass, "provider", "agent")
            is owner
        )
    sibling = agent_maintenance.get_agent_maintenance_gate(hass, "provider", "sibling")
    assert created == [owner, sibling]


async def test_concurrent_cold_gate_lookup_publishes_one_owner(hass, monkeypatch):
    original = agent_maintenance.AgentMaintenanceGate
    constructing = Barrier(2, timeout=10)

    class ConcurrentGate(original):
        def __init__(self):
            super().__init__()
            constructing.wait()

    monkeypatch.setattr(agent_maintenance, "AgentMaintenanceGate", ConcurrentGate)
    first, second = await asyncio.gather(
        *(
            asyncio.to_thread(
                agent_maintenance.get_agent_maintenance_gate, hass, "provider", "agent"
            )
            for _ in range(2)
        )
    )
    assert first is second
    assert (
        agent_maintenance.get_agent_maintenance_gate(hass, "provider", "agent") is first
    )


def test_store_binding_is_deduplicated_and_exactly_scoped(hass):
    store = RecoveryGuardedStore(hass, 1, "private").bind_agent("provider", "agent")
    gate = agent_maintenance.get_agent_maintenance_gate(hass, "provider", "agent")
    assert store.bind_agent("provider", "agent") is store
    assert gate.storage_resources == (store,)
    for identity in (("provider", "sibling"), ("other", "agent")):
        assert not agent_maintenance.get_agent_maintenance_gate(
            hass, *identity
        ).storage_resources


def test_rebinding_moves_cleanup_to_the_same_gate_as_io(hass):
    store = RecoveryGuardedStore(hass, 1, "private").bind_agent("provider", "agent")
    previous = store._recovery_gate
    store.bind_agent("provider", "replacement")
    assert previous.storage_resources == ()
    assert store._recovery_gate.storage_resources == (store,)


def test_live_store_snapshot_does_not_retain_dead_partition_handles(hass):
    store = RecoveryGuardedStore(hass, 1, "partition").bind_agent("provider", "agent")
    gate = store._recovery_gate
    weak = ref(store)
    snapshot = gate.storage_resources
    del store
    gc.collect()
    assert weak() is snapshot[0]
    del snapshot
    gc.collect()
    assert weak() is None
    assert gate.storage_resources == ()


def test_embedding_cache_registers_without_changing_best_effort_store(hass):
    cache = HomeAssistantEmbeddingCacheStorage(hass, "provider", "agent")
    gate = agent_maintenance.get_agent_maintenance_gate(hass, "provider", "agent")
    assert gate.storage_resources == (cache._store,)
    assert type(cache._store) is Store


async def test_erasure_requires_ownership_and_preserves_failed_handles_for_retry(
    hass, monkeypatch
):
    store = RecoveryGuardedStore(hass, 1, "private").bind_agent("provider", "agent")
    gate = store._recovery_gate
    delay = Mock()
    final = Mock()
    remove = AsyncMock(side_effect=[OSError("erase failed"), None])
    monkeypatch.setattr(store, "_async_cleanup_delay_listener", delay)
    monkeypatch.setattr(store, "_async_cleanup_final_write_listener", final)
    monkeypatch.setattr(store, "async_remove", remove)
    with pytest.raises(RuntimeError, match="exclusive"):
        await async_remove_agent_stores(gate)
    remove.assert_not_called()
    async with gate.exclusive():
        with pytest.raises(OSError, match="erase failed"):
            await async_remove_agent_stores(gate)
        assert gate.storage_resources == (store,)
        await async_remove_agent_stores(gate)
    assert delay.call_count == final.call_count == remove.await_count == 2
