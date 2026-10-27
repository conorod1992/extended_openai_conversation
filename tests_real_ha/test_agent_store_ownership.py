"""Permanent deletion retires exact live Store handles, independent of managers."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_deletion import (
    async_delete_agent_data,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantEmbeddingCacheStorage,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    PropagatingWriteStore,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store


async def test_concurrent_deletion_rechecks_fence_after_exclusive_admission(
    hass, monkeypatch
):
    gate = get_agent_maintenance_gate(hass, "provider", "departing")
    store = PropagatingWriteStore(
        hass, 1, f"{DOMAIN}.memory.provider.departing"
    ).bind_agent("provider", "departing")
    removing = asyncio.Event()
    release = asyncio.Event()
    second_retired = asyncio.Event()
    calls = 0
    retirements = 0
    original_remove = store.async_remove
    original_retire = gate.async_retire_readers

    async def blocked_remove():
        nonlocal calls
        calls += 1
        removing.set()
        await release.wait()
        await original_remove()

    async def observe_retirement():
        nonlocal retirements
        retirements += 1
        await original_retire()
        if retirements == 2:
            second_retired.set()

    monkeypatch.setattr(store, "async_remove", blocked_remove)
    monkeypatch.setattr(gate, "async_retire_readers", observe_retirement)
    first = asyncio.create_task(async_delete_agent_data(hass, "provider", "departing"))
    tasks = [first]
    try:
        await removing.wait()
        second = asyncio.create_task(
            async_delete_agent_data(hass, "provider", "departing")
        )
        tasks.append(second)
        await second_retired.wait()
        assert gate._waiting_writers == 1
        assert not second.done()
        release.set()
        assert await asyncio.gather(*tasks, return_exceptions=True) == [None, None]
        assert calls == 1
        assert gate.deleted
        assert not gate._writer_active
        with pytest.raises(HomeAssistantError, match="deleted"):
            await store.async_load()
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await Store.async_remove(store)


@pytest.mark.parametrize("cached", [False, True])
async def test_delete_retires_bound_store_without_knowing_manager_attributes(
    hass, cached
):
    owner = ("provider", "departing")
    store = PropagatingWriteStore(
        hass, 1, f"{DOMAIN}.memory.{owner[0]}.{owner[1]}.new_cache"
    ).bind_agent(*owner)
    sibling = PropagatingWriteStore(
        hass, 1, f"{DOMAIN}.memory.provider.sibling"
    ).bind_agent("provider", "sibling")
    other_provider = PropagatingWriteStore(
        hass, 1, f"{DOMAIN}.memory.other.departing"
    ).bind_agent("other", "departing")
    stores = (store, sibling, other_provider)
    for item in stores:
        item.async_delay_save(lambda: {"pending": True}, 3600)
        assert item._delay_handle is not None
        assert item._unsub_final_write_listener is not None
    if cached:
        hass.data[f"{DOMAIN}.memory_managers"] = {
            owner: SimpleNamespace(new_resource_layout=store)
        }
    try:
        await async_delete_agent_data(hass, *owner)
        assert store._delay_handle is None
        assert store._unsub_final_write_listener is None
        for retained in (sibling, other_provider):
            assert retained._delay_handle is not None
            assert retained._unsub_final_write_listener is not None
        await async_delete_agent_data(hass, *owner)
    finally:
        for item in stores:
            # The native cleanup must remain possible after EOAI's deletion fence.
            item._async_cleanup_delay_listener()
            item._async_cleanup_final_write_listener()
            await Store.async_remove(item)


async def test_delete_retires_live_archive_partition_outside_manager_cache(hass):
    storage = HomeAssistantArchiveStorage(hass, "provider", "departing")
    partition = storage._partition_store("2030-01")
    partition.async_delay_save(lambda: {"turns": []}, 3600)
    try:
        await async_delete_agent_data(hass, "provider", "departing")
        assert partition._delay_handle is None
        assert partition._unsub_final_write_listener is None
    finally:
        partition._async_cleanup_delay_listener()
        partition._async_cleanup_final_write_listener()
        await Store.async_remove(partition)
        await Store.async_remove(storage._metadata)


async def test_delete_retires_plain_embedding_cache_without_changing_io_policy(hass):
    cache = HomeAssistantEmbeddingCacheStorage(hass, "provider", "departing")
    store = cache._store
    assert type(store) is Store
    store.async_delay_save(lambda: {"vectors": []}, 3600)
    try:
        await async_delete_agent_data(hass, "provider", "departing")
        assert store._delay_handle is None
        assert store._unsub_final_write_listener is None
    finally:
        store._async_cleanup_delay_listener()
        store._async_cleanup_final_write_listener()
        await Store.async_remove(store)
