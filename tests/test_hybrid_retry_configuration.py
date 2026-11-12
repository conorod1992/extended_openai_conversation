"""Retry identity tracks runtime/credentials independently of vector space."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
