"""Native cache workers must settle before permanent Memory deletion."""

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_deletion import (
    async_delete_agent_data,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    conversation_request_lease,
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantEmbeddingCacheStorage,
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from homeassistant.helpers import storage as ha_storage
from homeassistant.helpers.storage import Store
from tests_real_ha.native_storage import hass_storage  # noqa: F401


@pytest.mark.parametrize("request_lease", [False, True])
@pytest.mark.parametrize("write_failure", [False, True])
async def test_hybrid_cache_worker_settles_before_permanent_deletion(
    hass, monkeypatch, caplog, request_lease, write_failure
):
    owner = ("cache-provider", "cache-agent")
    primary = HomeAssistantMemoryStorage(hass, *owner)
    cache = HomeAssistantEmbeddingCacheStorage(hass, *owner)
    memory = PersistentMemory(primary, cache)
    hass.data.setdefault(f"{DOMAIN}.memory_managers", {})[owner] = memory
    await memory.async_initialize()
    await memory.async_add(
        "owner", "The owner prefers green tea.", "preference", "explicit"
    )
    primary_path = Path(primary._store.path)
    cache_path = Path(cache._store.path)
    assert primary_path.is_file()
    assert type(cache._store) is Store

    async def embeddings(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    memory.set_embedding_provider(embeddings, "cache-model", space_id="cache-space")
    gate = get_agent_maintenance_gate(hass, *owner)
    agent = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id=owner[0]),
        subentry=SimpleNamespace(subentry_id=owner[1]),
    )
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    native_write = ha_storage.write_utf8_file_atomic
    writes = 0

    def blocked_write(path, *args, **kwargs):
        nonlocal writes
        if Path(path) != cache_path:
            return native_write(path, *args, **kwargs)
        writes += 1
        hass.loop.call_soon_threadsafe(entered.set)
        if not release.wait(15):
            raise RuntimeError("Cache worker barrier was not released")
        try:
            result = native_write(path, *args, **kwargs)
            if write_failure:
                raise OSError("private-cache-failure-detail")
            return result
        finally:
            hass.loop.call_soon_threadsafe(finished.set)

    monkeypatch.setattr(ha_storage, "write_utf8_file_atomic", blocked_write)

    async def refresh():
        if request_lease:
            async with conversation_request_lease(agent):
                return await memory.async_prepare_hybrid(["owner"], "Which tea?")
        return await memory.async_prepare_hybrid(["owner"], "Which tea?")

    refreshing = asyncio.create_task(refresh())
    deleting = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert gate._active_readers and not finished.is_set()
        deleting = asyncio.create_task(async_delete_agent_data(hass, *owner))
        done, _ = await asyncio.wait({deleting}, timeout=0.5)
        assert not done, (
            "Deletion must retain ownership until the native worker settles"
        )
        assert not gate.deleted and gate._active_readers
        release.set()
        await asyncio.wait_for(finished.wait(), 5)
        outcomes = await asyncio.wait_for(
            asyncio.gather(refreshing, deleting, return_exceptions=True), 5
        )
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert outcomes[1] is None
        assert writes == 1 and gate.deleted
        assert not primary_path.exists() and not cache_path.exists()
        assert not gate._active_readers and not gate._writer_active
        assert memory._embedding_cache_write_failed is write_failure
        assert "private-cache-failure-detail" not in caplog.text
    finally:
        release.set()
        tasks = [refreshing] + ([deleting] if deleting is not None else [])
        await asyncio.gather(*tasks, return_exceptions=True)
        if entered.is_set():
            await asyncio.wait_for(finished.wait(), 5)
        await Store.async_remove(cache._store)
