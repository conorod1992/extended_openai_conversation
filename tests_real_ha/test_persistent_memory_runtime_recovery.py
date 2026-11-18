"""Recover invalidated Memory through ordinary Assist, using durable storage."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import memory
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SUBSYSTEM_STATUS_KEY,
)
from homeassistant.components import conversation
from homeassistant.core import Context, CoreState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.file import WriteError
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _speech
from tests_real_ha.test_provider_input_history import _transport
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _chat_sse_tool_call
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
async def test_rebuild_embeddings_requires_durable_removal(hass, monkeypatch):
    entry = _make_entry("Cache reset", include_ai_task=False)
    await _setup_entry(hass, entry)
    agent_id = next(iter(entry.subentries))
    manager = await memory.async_get_memory(hass, entry.entry_id, agent_id)
    _, fact = await manager.async_add_record(
        "review-user", "The orchid is purple.", "general", "explicit"
    )
    provider = AsyncMock(
        side_effect=lambda texts: memory.EmbeddingBatch(
            [[1.0, 0.0] for _ in texts], "alias"
        )
    )
    manager.set_embedding_provider(provider, "alias")
    assert await manager.async_prepare_hybrid(["review-user"], "old query") == [
        1.0,
        0.0,
    ]
    cache = manager._embedding_cache_storage
    path = Path(cache._store.path)
    old_file = await hass.async_add_executor_job(path.read_bytes)
    original_unlink = os.unlink

    def reject_cache_removal(target, *args, **kwargs):
        if target == str(path):
            raise PermissionError("Cache filesystem is read-only")
        return original_unlink(target, *args, **kwargs)

    async def rebuild():
        await hass.services.async_call(
            DOMAIN,
            "rebuild_memory_embeddings",
            {"config_entry": entry.entry_id, "agent_id": agent_id},
            blocking=True,
        )

    with monkeypatch.context() as unavailable:
        unavailable.setattr(os, "unlink", reject_cache_removal)
        with pytest.raises(
            HomeAssistantError, match="Could not persist the embedding cache reset"
        ):
            await rebuild()
    assert await hass.async_add_executor_job(path.read_bytes) == old_file
    assert len(manager._embedding_cache) == 1

    writer = AsyncMock(side_effect=WriteError("Native writer unavailable"))
    monkeypatch.setattr(cache._store, "_async_write_data", writer)
    # Rebuilding must also retire a pending native generation.
    cache._store.async_delay_save(lambda: {"embeddings": {}}, delay=3600)
    started, release = asyncio.Event(), asyncio.Event()
    native_remove = cache.async_remove

    async def delayed_remove():
        started.set()
        await release.wait()
        await native_remove()

    monkeypatch.setattr(cache, "async_remove", delayed_remove)
    with monkeypatch.context() as stopping:
        stopping.setattr(hass, "state", CoreState.stopping)
        operation = asyncio.create_task(manager.async_rebuild_embedding_cache())
        await started.wait()
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
    writer.assert_not_awaited()
    assert not await hass.async_add_executor_job(path.exists)
    assert await cache.async_load() is None
    await rebuild()

    fresh_cache = memory.HomeAssistantEmbeddingCacheStorage(
        hass, entry.entry_id, agent_id
    )
    reloaded = memory.PersistentMemory(manager._storage, fresh_cache)
    await reloaded.async_initialize()
    assert await reloaded.async_list("review-user") == [fact]
    provider.reset_mock()
    provider.side_effect = lambda texts: memory.EmbeddingBatch(
        [[0.0, 1.0] for _ in texts], "alias"
    )
    reloaded.set_embedding_provider(provider, "alias")
    assert await reloaded.async_prepare_hybrid(["review-user"], "fresh query") == [
        0.0,
        1.0,
    ]
    assert [call.args[0] for call in provider.await_args_list] == [
        ["general | The orchid is purple."],
        ["fresh query"],
    ]
    await hass.async_add_executor_job(path.write_bytes, b"{invalid")
    recovered = memory.PersistentMemory(
        manager._storage, memory.HomeAssistantEmbeddingCacheStorage(hass, entry.entry_id, agent_id)
    )
    await recovered.async_initialize()
    assert await recovered.async_list("review-user") == [fact]
    recovered.set_embedding_provider(provider, "alias")
    provider.reset_mock()
    assert await recovered.async_prepare_hybrid(["review-user"], "after corruption") == [0.0, 1.0]
    assert provider.await_count == 2


class FaultStorage:
    """Inject storage boundary failures without replacing production methods."""

    def __init__(self, hass, path, commit):
        self.hass = hass
        self.path = path
        self.commit = commit
        self.outage = False
        self.reads = 0

    async def async_load(self):
        self.reads += 1
        if self.outage:
            raise OSError("Memory read unavailable")
        if not self.path.exists():
            return None
        return json.loads(await self.hass.async_add_executor_job(self.path.read_text))

    async def async_save(self, data):
        if not self.outage or self.commit:
            await self.hass.async_add_executor_job(
                self.path.write_text, json.dumps(data)
            )
        if self.outage:
            raise OSError("Memory write acknowledgement unavailable")


@pytest.mark.parametrize("commit", [False, True], ids=["before-commit", "lost-ack"])
async def test_loaded_memory_recovers_on_next_assist_without_config_change(
    hass, tmp_path, commit
):
    owner = await hass.auth.async_create_user("Memory owner")
    entry = _make_entry(
        include_ai_task=False,
        conversation_options=normalize_agent_config(
            {
                "chat_model": "gpt-5.6",
                "api_mode": "chat_completions",
                "reasoning_effort": "none",
                "functions": [],
                "memory_mode": "automatic",
                "temporary_memory": "off",
                "knowledge_enabled": False,
                "archive_enabled": False,
                "archive_model_search_enabled": False,
            }
        ),
    )
    subentry = next(iter(entry.subentries.values()))
    storage = FaultStorage(hass, tmp_path / "authoritative-memory.json", commit)
    manager = memory.PersistentMemory(storage)
    hass.data.setdefault(memory._MEMORY_MANAGERS, {})[
        (entry.entry_id, subentry.subentry_id)
    ] = manager
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    await manager.async_add(owner.id, "My orchid is purple", "general", "explicit")

    async def ask(replies):
        async with _transport(entry, replies) as wire:
            result = await conversation.async_converse(
                hass=hass,
                text="Remember my orchid",
                context=Context(user_id=owner.id),
                language="en",
                agent_id=entry.entry_id,
                conversation_id=None,
            )
            assert _speech(result) == "Done"
            wire.assert_complete(len(replies))
            return wire

    await ask([_chat_sse_text("Done")])
    assert agent._memory is manager and manager.initialized
    original_config = subentry.data

    def status():
        return hass.data[SUBSYSTEM_STATUS_KEY][(entry.entry_id, subentry.subentry_id)][
            "persistent_memory"
        ]["status"]

    storage.outage = True
    with pytest.raises(OSError):
        await manager.async_add(owner.id, "My bicycle is red", "general", "explicit")
    assert not manager.initialized
    for _ in range(2):
        reads = storage.reads
        await ask([_chat_sse_text("Done")])
        assert storage.reads == reads + 1
        assert not manager.initialized
        assert status() == "failed"
    storage.outage = False
    reads = storage.reads
    wire = await ask(
        [
            _chat_sse_tool_call("recovered-list", "memory_list", {}),
            _chat_sse_text("Done"),
        ]
    )
    assert manager.initialized and agent._memory is manager
    assert subentry.data is original_config
    assert storage.reads == reads + 1
    assert status() == "healthy"
    expected = {"My orchid is purple"} | ({"My bicycle is red"} if commit else set())
    assert {item.content for item in await manager.async_list(owner.id)} == expected
    prompt = json.dumps(wire.requests[0]["body"])
    assert "My orchid is purple" in prompt
    output = next(
        item["content"]
        for item in wire.requests[1]["body"]["messages"]
        if item.get("role") == "tool"
    )
    for fact in expected:
        assert fact in output
    await ask([_chat_sse_text("Done")])
    assert storage.reads == reads + 1


@pytest.mark.parametrize(
    "category",
    [
        "myApiKey: PRIVATE_MEMORY_VALUE",
        "myapikey: private_memory_value",
        "deviceaccesstoken=private_memory_value",
    ],
)
async def test_cold_memory_storage_preserves_credential_categories_without_purge(
    hass, monkeypatch, caplog, category, real_store_io
):
    from copy import deepcopy
    from threading import get_ident

    storage = memory.HomeAssistantMemoryStorage(hass, "privacy-entry", "privacy-agent")
    manager = memory.PersistentMemory(storage)
    await manager.async_initialize()
    await manager.async_add("alice", "My plant is purple", "Preferences", "explicit")
    raw = await manager.async_backup_data()
    unsafe = deepcopy(raw["memories"][0])
    unsafe.update(memory_id="legacy-unsafe", category=category, key="unsafe.key")
    raw["memories"].append(unsafe)
    await storage.async_save(raw)
    calls = []
    original = memory._clean_category
    loop_thread = get_ident()

    def checked(value):
        calls.append(get_ident())
        return original(value)

    monkeypatch.setattr(memory, "_clean_category", checked)
    loaded = memory.PersistentMemory(storage)
    await loaded.async_initialize()
    assert calls and loop_thread not in calls
    retained = await loaded.async_backup_data()
    assert len(retained["memories"]) == 2
    assert retained["memories"][0]["content"] == "My plant is purple"
    assert (await storage.async_load()) == raw
    assert loaded._key_index[("alice", "unsafe.key")] == "legacy-unsafe"
    assert next(r for r in retained["memories"] if r["memory_id"] == "legacy-unsafe")["category"] == category.casefold()
    assert "PRIVATE_MEMORY_VALUE" not in caplog.text
    assert "private_memory_value" not in caplog.text
