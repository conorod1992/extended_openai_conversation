"""Retry identity tracks runtime/credentials independently of vector space."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.agent_configuration import (
    sync_memory_embedding_provider,
)
from tests.test_hybrid_memory_cold_path import _memory


async def test_configuration_sync_preserves_backoff_until_credentials_or_client_change():
    memory = await _memory()
    entity = SimpleNamespace(
        _memory=memory,
        subentry=SimpleNamespace(data={"memory_retrieval_mode": "hybrid"}),
        entry=SimpleNamespace(data={"api_key": "first"}, runtime_data=object()),
        _async_create_embeddings=AsyncMock(side_effect=OSError("unavailable")),
    )
    sync_memory_embedding_provider(entity)
    await memory.async_prepare_hybrid(["alice"], "query")
    space = memory._embedding_space_id
    first = memory.hybrid_status()
    sync_memory_embedding_provider(entity)
    assert memory.hybrid_status() == first
    assert await memory.async_prepare_hybrid(["alice"], "query") is None
    assert entity._async_create_embeddings.await_count == 1
    entity.entry.data["api_key"] = "second"
    sync_memory_embedding_provider(entity)
    assert memory._embedding_space_id == space
    await memory.async_prepare_hybrid(["alice"], "query")
    assert entity._async_create_embeddings.await_count == 2
    entity.entry.runtime_data = object()
    sync_memory_embedding_provider(entity)
    await memory.async_prepare_hybrid(["alice"], "query")
    assert entity._async_create_embeddings.await_count == 3
    entity.entry.data["base_url"] = "https://example.test/v1"
    sync_memory_embedding_provider(entity)
    assert memory._embedding_space_id != space
    await memory.async_prepare_hybrid(["alice"], "query")
    assert entity._async_create_embeddings.await_count == 4


@pytest.mark.parametrize("phase", ["corpus", "query"])
async def test_equivalent_sync_during_overlapping_retrieval_retains_provider(phase):
    memory = await _memory()
    await memory.async_add("alice", "Likes tea.", "preferences", "explicit")
    entered, release = asyncio.Event(), asyncio.Event()
    held = False

    async def embeddings(texts, *, model):
        nonlocal held
        if not held and (phase == "corpus" or texts == ["first query"]):
            held = True
            entered.set()
            await release.wait()
        return [[1.0, 0.0] for _ in texts]

    entity = SimpleNamespace(
        _memory=memory,
        subentry=SimpleNamespace(
            data={"memory_retrieval_mode": "hybrid", "memory_embedding_model": "model"}
        ),
        entry=SimpleNamespace(data={}, runtime_data=object()),
        _async_create_embeddings=embeddings,
    )
    sync_memory_embedding_provider(entity)
    provider = memory._embedding_provider
    first = asyncio.create_task(memory.async_prepare_hybrid(["alice"], "first query"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        entity.subentry.data["memory_embedding_model"] = " model "
        sync_memory_embedding_provider(entity)
        assert memory._embedding_provider is provider
        assert await memory.async_prepare_hybrid(["alice"], "second query") == [
            1.0,
            0.0,
        ]
        release.set()
        assert await first == [1.0, 0.0]
        assert memory.hybrid_status()["status"] == "active"
        assert memory.hybrid_status()["retry_after_seconds"] == 0
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize("change", ["entity", "client"])
async def test_rebinding_retires_old_failure_without_backing_off_new_provider(change):
    memory = await _memory()
    entered, release = asyncio.Event(), asyncio.Event()

    async def old(texts, *, model):
        if texts == ["old query"]:
            entered.set()
            await release.wait()
            raise OSError("old provider failed")
        return [[1.0, 0.0] for _ in texts]

    entity = SimpleNamespace(
        _memory=memory,
        subentry=SimpleNamespace(data={"memory_retrieval_mode": "hybrid"}),
        entry=SimpleNamespace(data={}, runtime_data=object()),
        _async_create_embeddings=old,
    )
    sync_memory_embedding_provider(entity)
    first = asyncio.create_task(memory.async_prepare_hybrid(["alice"], "old query"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        provider = memory._embedding_provider
        if change == "client":
            entity.entry.runtime_data = object()
        else:
            entity = SimpleNamespace(**vars(entity))
            entity._async_create_embeddings = AsyncMock(return_value=[[1.0, 0.0]])
        sync_memory_embedding_provider(entity)
        assert memory._embedding_provider is not provider
        assert await memory.async_prepare_hybrid(["alice"], "new query") == [1.0, 0.0]
        release.set()
        assert await first is None
        assert memory.hybrid_status()["status"] == "active"
        assert memory.hybrid_status()["retry_after_seconds"] == 0
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
