"""Cache durability is independent of usable current semantic vectors."""

import asyncio
from dataclasses import asdict, replace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import memory as module
from custom_components.extended_openai_conversation_responses.memory import (
    EMBEDDING_CACHE_BATCH_SIZE,
    EMBEDDING_CACHE_RETRY_SECONDS,
    MemoryRecord,
    PersistentMemory,
)
from tests.test_memory import FakeStorage


async def collection(count=1):
    base = MemoryRecord("0", "alice", "Unique fact 0", "general", "explicit", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    rows = [asdict(replace(base, memory_id=str(i), content=f"Unique fact {i}")) for i in range(count)]
    durable, cache = FakeStorage({"memories": rows}), FakeStorage()
    cache.async_save = AsyncMock(side_effect=OSError("cache unavailable"))
    memory = PersistentMemory(durable, cache)
    await memory.async_initialize()
    return memory, durable, cache


async def test_failure_finishes_all_batches_and_retries_only_after_cooldown(monkeypatch, caplog):
    now = 100.0
    monkeypatch.setattr(module, "monotonic", lambda: now)
    count = 2 * EMBEDDING_CACHE_BATCH_SIZE + 1
    memory, durable, cache = await collection(count)
    provider = AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
    memory.set_embedding_provider(provider, "model", space_id="space")
    saves = durable.save_count
    assert await memory.async_prepare_hybrid(["alice"], "unrelated words") == [1.0, 0.0]
    assert [len(call.args[0]) for call in provider.call_args_list] == [64, 64, 1, 1]
    assert len(memory._embedding_cache) == count
    assert cache.async_save.call_count == 1 and memory._embedding_cache_dirty
    assert memory.hybrid_status()["status"] == "active"
    assert memory.hybrid_status()["reason"] == "embedding_cache_not_persisted"
    assert "using lexical retrieval" not in caplog.text
    found = await memory.async_search("alice", "unrelated words", query_embedding=[1.0, 0.0], hybrid=True)
    assert found  # No lexical overlap: the in-memory semantic vectors are used.
    for _ in range(3):
        assert await memory.async_prepare_hybrid(["alice"], "again") == [1.0, 0.0]
    assert cache.async_save.call_count == 1 and provider.call_count == 7
    now += EMBEDDING_CACHE_RETRY_SECONDS
    await memory.async_prepare_hybrid(["alice"], "still failing")
    assert cache.async_save.call_count == 2 and memory._embedding_cache_dirty
    now += EMBEDDING_CACHE_RETRY_SECONDS
    cache.async_save.side_effect = None
    await memory.async_prepare_hybrid(["alice"], "recovered")
    assert cache.async_save.call_count == 3 and not memory._embedding_cache_dirty
    assert "reason" not in memory.hybrid_status()
    assert len(cache.async_save.call_args.args[0]["embeddings"]) == count
    assert durable.save_count == saves


@pytest.mark.parametrize("bad", [[], [[float("nan")]], [["bad"]]])
async def test_cache_failure_does_not_mask_provider_or_vector_failure(bad):
    memory, _, _ = await collection(EMBEDDING_CACHE_BATCH_SIZE + 1)
    provider = AsyncMock(side_effect=[[[1.0, 0.0]] * EMBEDDING_CACHE_BATCH_SIZE, bad])
    memory.set_embedding_provider(provider, "model")
    assert await memory.async_prepare_hybrid(["alice"], "query") is None
    assert memory.hybrid_status()["status"] == "lexical_fallback"
    assert "64" not in memory._embedding_cache


@pytest.mark.parametrize("mutation", ["edit", "delete", "move", "model", "provider", "space", "cancel"])
async def test_inflight_vectors_are_rejected_after_mutation_or_reconfiguration(mutation):
    memory, _, cache = await collection()
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(texts):
        entered.set()
        await release.wait()
        return [[1.0, 0.0] for _ in texts]

    memory.set_embedding_provider(provider, "model", space_id="space")
    pending = asyncio.create_task(memory.async_prepare_hybrid(["alice"], "query"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if mutation == "edit":
            await memory.async_update("alice", "0", content="Changed fact")
        elif mutation == "delete":
            await memory.async_delete("alice", ["0"])
        elif mutation == "move":
            await memory.async_update("alice", "0", target_user_id="bob")
        elif mutation in {"model", "provider", "space"}:
            memory.set_embedding_provider(AsyncMock() if mutation == "provider" else provider,
                                          "new" if mutation == "model" else "model",
                                          space_id="new" if mutation == "space" else "space")
        else:
            pending.cancel()
        release.set()
        if mutation == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            vector = await pending
            if mutation in {"model", "provider", "space"}:
                assert vector is None
        assert not memory._embedding_cache and cache.async_save.call_count == 0
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_query_rejected_after_space_change_with_same_provider_and_model():
    memory, _, _ = await collection()
    async def provider(texts):
        if texts == ["query"]:
            memory.set_embedding_provider(provider, "model", space_id="new-space")
        return [[1.0, 0.0] for _ in texts]
    memory.set_embedding_provider(provider, "model", space_id="old-space")
    assert await memory.async_prepare_hybrid(["alice"], "query") is None
