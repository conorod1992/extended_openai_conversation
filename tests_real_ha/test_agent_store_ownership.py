"""Permanent deletion retires exact live Store handles, independent of managers."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_deletion import (
    async_delete_agent_data,
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
from homeassistant.helpers.storage import Store


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
