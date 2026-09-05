"""Hybrid-memory on-demand embedding tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from custom_components.extended_openai_conversation_responses.memory import (
    EMBEDDING_CACHE_BATCH_SIZE,
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


class CountingCacheStorage(FakeStorage):
    """Embedding cache that records persistence frequency."""

    def __init__(self, data=None) -> None:
        super().__init__(data)
        self.save_calls = 0

    async def async_save(self, data):
        self.save_calls += 1
        await super().async_save(data)


class FailingCacheStorage(FakeStorage):
    """Embedding cache that cannot persist generated data."""

    async def async_save(self, data):
        raise OSError("cache unavailable")


async def _memory(cache=None) -> PersistentMemory:
    memory = PersistentMemory(FakeStorage(), cache or FakeStorage())
    await memory.async_initialize()
    return memory


async def test_provider_configuration_does_not_prewarm_existing_memories() -> None:
    memory = await _memory()
    await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
    )
    await memory.async_add(
        "bob",
        "Milo is a Labrador.",
        "pets",
        "explicit",
        subject="Milo",
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await asyncio.sleep(0)

    assert calls == []
    assert await memory.async_prepare_hybrid(["alice"], "What breed is Oscar?")
    assert calls == [
        ["Oscar | pets | Oscar is a Cavachon."],
        ["What breed is Oscar?"],
    ]


async def test_scope_warmup_batches_only_requested_scope() -> None:
    cache = CountingCacheStorage()
    memory = await _memory(cache)
    memory_count = EMBEDDING_CACHE_BATCH_SIZE + 1
    for index in range(memory_count):
        await memory.async_add(
            "alice",
            f"Unique memory {index} records code value-{index}.",
            "test",
            "explicit",
        )
    await memory.async_add(
        "bob", "Bob has an unrelated private memory.", "test", "explicit"
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, float(index + 1)] for index, _ in enumerate(inputs)]

    memory.set_embedding_provider(embeddings, "test-model")

    assert await memory.async_prepare_hybrid(["alice"], "code")
    assert [len(batch) for batch in calls] == [EMBEDDING_CACHE_BATCH_SIZE, 1, 1]
    assert all("Bob has an unrelated private memory." not in text for batch in calls for text in batch)
    assert cache.save_calls == 2
    assert len(cache.data["embeddings"]) == memory_count


async def test_embedding_relevant_write_waits_for_next_scope_query() -> None:
    memory = await _memory()
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    created = await memory.async_add(
        "alice", "Tea is kept in the pantry.", "home", "explicit"
    )
    await asyncio.sleep(0)
    assert calls == []

    assert await memory.async_prepare_hybrid(["alice"], "tea")
    assert calls == [["home | Tea is kept in the pantry."], ["tea"]]
    calls.clear()

    await memory.async_update(
        "alice",
        created["memory"]["memory_id"],
        content="Tea is kept in the kitchen cupboard.",
    )
    await asyncio.sleep(0)
    assert calls == []

    assert await memory.async_prepare_hybrid(["alice"], "tea")
    assert calls == [["home | Tea is kept in the kitchen cupboard."], ["tea"]]


async def test_model_change_reembeds_requested_scope_on_demand() -> None:
    memory = await _memory()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    await memory.async_add("bob", "Milo is a Labrador.", "pets", "explicit")
    calls: list[tuple[str, list[str]]] = []
    model = "first-model"

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append((model, inputs))
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, model)
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    calls.clear()

    model = "second-model"
    memory.set_embedding_provider(embeddings, model)
    await asyncio.sleep(0)
    assert calls == []

    assert await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [
        ("second-model", ["pets | Oscar is a Cavachon."]),
        ("second-model", ["breed"]),
    ]


async def test_failed_request_time_warmup_falls_back_cleanly() -> None:
    memory = await _memory()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        raise OSError("provider unavailable")

    memory.set_embedding_provider(embeddings, "test-model")

    assert await memory.async_prepare_hybrid(["alice"], "breed") is None
    assert calls == [["pets | Oscar is a Cavachon."]]


async def test_failed_cache_save_keeps_request_time_fallback() -> None:
    memory = await _memory(FailingCacheStorage())
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")

    assert await memory.async_prepare_hybrid(["alice"], "breed") is None
    assert calls == [["pets | Oscar is a Cavachon."]]
