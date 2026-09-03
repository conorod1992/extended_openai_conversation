"""Hybrid-memory cold-path maintenance tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)


class FakeStorage:
    """Detached in-memory storage."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class FailingCacheStorage(FakeStorage):
    """Embedding cache that cannot persist generated data."""

    async def async_save(self, data):
        raise OSError("cache unavailable")


async def _background_memory(cache=None) -> PersistentMemory:
    memory = PersistentMemory(
        FakeStorage(),
        cache or FakeStorage(),
        asyncio.create_task,
    )
    await memory.async_initialize()
    return memory


async def test_provider_configuration_prewarms_existing_memories() -> None:
    memory = await _background_memory()
    await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await memory.async_wait_for_embedding_maintenance()

    assert calls == [["Oscar | pets | Oscar is a Cavachon."]]
    calls.clear()
    assert await memory.async_prepare_hybrid(["alice"], "What breed is Oscar?")
    assert calls == [["What breed is Oscar?"]]


async def test_embedding_relevant_write_is_warmed_after_write_returns() -> None:
    memory = await _background_memory()
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        provider_started.set()
        await release_provider.wait()
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await memory.async_wait_for_embedding_maintenance()

    created = await asyncio.wait_for(
        memory.async_add("alice", "Tea is kept in the pantry.", "home", "explicit"),
        timeout=1,
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)
    release_provider.set()
    await memory.async_wait_for_embedding_maintenance()

    provider_started.clear()
    release_provider.clear()
    await memory.async_update(
        "alice",
        created["memory"]["memory_id"],
        content="Tea is kept in the kitchen cupboard.",
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)
    release_provider.set()
    await memory.async_wait_for_embedding_maintenance()


async def test_model_change_prewarms_stale_cache_before_next_query() -> None:
    memory = await _background_memory()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    calls: list[tuple[str, list[str]]] = []
    model = "first-model"

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append((model, inputs))
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, model)
    await memory.async_wait_for_embedding_maintenance()
    calls.clear()

    model = "second-model"
    memory.set_embedding_provider(embeddings, model)
    await memory.async_wait_for_embedding_maintenance()
    assert calls == [("second-model", ["pets | Oscar is a Cavachon."])]

    calls.clear()
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [("second-model", ["breed"])]


async def test_failed_background_warmup_keeps_request_time_fallback() -> None:
    memory = await _background_memory()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    fail = True
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        if fail:
            raise OSError("provider unavailable")
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await memory.async_wait_for_embedding_maintenance()
    calls.clear()

    fail = False
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [["pets | Oscar is a Cavachon."], ["breed"]]


async def test_failed_background_cache_save_does_not_change_cold_fallback() -> None:
    memory = await _background_memory(FailingCacheStorage())
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await memory.async_wait_for_embedding_maintenance()
    calls.clear()

    assert await memory.async_prepare_hybrid(["alice"], "breed") is None
    assert calls == [["pets | Oscar is a Cavachon."]]
