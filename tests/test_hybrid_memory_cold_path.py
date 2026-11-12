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
    assert all(
        "Bob has an unrelated private memory." not in text
        for batch in calls
        for text in batch
    )
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


async def test_pending_query_discarded_after_same_dimension_model_replacement() -> None:
    """An old query cannot use the newly populated, incompatible model space."""
    memory = await _memory()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    started, release = asyncio.Event(), asyncio.Event()

    async def old(inputs):
        if inputs == ["old query"]:
            started.set()
            await release.wait()
        return [[1.0, 0.0] for _ in inputs]

    async def replacement(inputs):
        return [[0.0, 1.0] for _ in inputs]

    memory.set_embedding_provider(old, "model-a")
    pending = asyncio.create_task(memory.async_prepare_hybrid(["alice"], "old query"))
    try:
        await asyncio.wait_for(started.wait(), 5)
        memory.set_embedding_provider(replacement, "model-b")
        assert await memory.async_prepare_hybrid(["alice"], "new query") == [0.0, 1.0]
        release.set()
        assert await pending is None
        assert all(
            entry.model == "model-b" for entry in memory._embedding_cache.values()
        )
        assert await memory.async_prepare_hybrid(["alice"], "healthy") == [0.0, 1.0]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_remote_failure_backoff_expiry_reset_and_preserved_vectors(monkeypatch):
    from unittest.mock import AsyncMock

    from custom_components.extended_openai_conversation_responses import (
        memory as module,
    )

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    memory = await _memory()
    await memory.async_add("alice", "Oscar is a Cavachon", "pets", "explicit")
    await memory.async_add("bob", "Private sibling fact", "test", "explicit")
    provider = AsyncMock(
        side_effect=[
            [[1.0, 0.0]],
            OSError("unavailable"),
            OSError("unavailable"),
            [[1.0, 0.0]],
        ]
    )
    memory.set_embedding_provider(provider, "model", retry_identity="client-a")
    assert await memory.async_prepare_hybrid(["alice"], "puppy") is None
    cache = dict(memory._embedding_cache)
    assert len(cache) == 1
    assert memory.hybrid_status()["retry_after_seconds"] == 30
    for _ in range(3):
        assert await memory.async_prepare_hybrid(["alice"], "puppy") is None
    assert provider.await_count == 2
    # Recreating a provider wrapper must not reset the failure or discard vectors.
    memory.set_embedding_provider(provider, "model", retry_identity="client-a")
    assert memory.hybrid_status()["retry_after_seconds"] == 30
    clock[0] += 30
    assert await memory.async_prepare_hybrid(["alice"], "puppy") is None
    assert provider.await_count == 3
    assert memory.hybrid_status()["retry_after_seconds"] == 60
    clock[0] += 60
    assert await memory.async_prepare_hybrid(["alice"], "puppy") == [1.0, 0.0]
    assert provider.await_count == 4
    assert memory._embedding_cache == cache
    assert memory.hybrid_status()["retry_after_seconds"] == 0
    assert memory.hybrid_status()["status"] == "active"


async def test_meaningful_retry_identity_change_retries_immediately(monkeypatch):
    from unittest.mock import AsyncMock

    memory = await _memory()
    provider = AsyncMock(side_effect=OSError("unavailable"))
    memory.set_embedding_provider(
        provider, "model", space_id="space", retry_identity="client-a"
    )
    await memory.async_prepare_hybrid(["alice"], "query")
    replacement = AsyncMock(return_value=[[1.0, 0.0]])
    memory.set_embedding_provider(
        replacement, "model", space_id="space", retry_identity="client-a"
    )
    assert await memory.async_prepare_hybrid(["alice"], "query") is None
    replacement.assert_not_awaited()
    memory.set_embedding_provider(
        replacement, "model", space_id="space", retry_identity="client-b"
    )
    assert await memory.async_prepare_hybrid(["alice"], "query") == [1.0, 0.0]
    assert "client-b" not in str(memory.hybrid_status())


async def test_backoff_is_bounded_and_cancellation_does_not_start_it(monkeypatch):
    from unittest.mock import AsyncMock

    import pytest

    from custom_components.extended_openai_conversation_responses import (
        memory as module,
    )

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    memory = await _memory()
    provider = AsyncMock(side_effect=asyncio.CancelledError())
    memory.set_embedding_provider(provider)
    with pytest.raises(asyncio.CancelledError):
        await memory.async_prepare_hybrid(["alice"], "query")
    assert memory.hybrid_status()["retry_after_seconds"] == 0
    provider.side_effect = OSError("unavailable")
    for _ in range(10):
        await memory.async_prepare_hybrid(["alice"], "query")
        delay = memory.hybrid_status()["retry_after_seconds"]
        assert 30 <= delay <= 300
        clock[0] += delay
    assert delay == 300


async def test_local_cache_write_failure_does_not_backoff_remote_provider():
    memory = await _memory(FailingCacheStorage())
    await memory.async_add("alice", "Oscar is a Cavachon", "pets", "explicit")
    from unittest.mock import AsyncMock

    provider = AsyncMock(return_value=[[1.0, 0.0]])
    memory.set_embedding_provider(provider)
    assert await memory.async_prepare_hybrid(["alice"], "query") is None
    assert memory.hybrid_status()["reason"] == "cache_write_failed"
    assert memory.hybrid_status()["retry_after_seconds"] == 0
    assert await memory.async_prepare_hybrid(["alice"], "query") == [1.0, 0.0]
    assert provider.await_count == 2
