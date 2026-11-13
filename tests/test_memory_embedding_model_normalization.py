"""Equivalent model names keep provider bindings, spaces, and diagnostics stable."""

from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.agent_configuration import (
    sync_memory_embedding_provider,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_MEMORY_EMBEDDING_MODEL,
    DEFAULT_MEMORY_EMBEDDING_MODEL,
    MEMORY_RETRIEVAL_HYBRID,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from tests.test_configuration_lifecycle_hardening import FakeEntity
from tests.test_memory import FakeStorage


@pytest.mark.parametrize(
    "initial,equivalent,expected",
    [("model", " model \t", "model"), ("", " \t", "default")],
)
def test_setter_normalizes_before_unchanged_comparison(initial, equivalent, expected):
    memory = PersistentMemory(FakeStorage())
    provider = AsyncMock()
    memory.set_embedding_provider(provider, initial, space_id="space")
    memory._set_hybrid_status("lexical_fallback", "provider_error")
    before = memory.hybrid_status()
    memory.set_embedding_provider(provider, equivalent, space_id="space")
    assert memory.hybrid_status() == before and memory._embedding_model == expected


@pytest.mark.parametrize(
    "initial,equivalent,expected",
    [("model", " model \t", "model"), ("", " \t", DEFAULT_MEMORY_EMBEDDING_MODEL)],
)
async def test_sync_normalizes_wrapper_space_and_comparison(
    initial, equivalent, expected
):
    memory = PersistentMemory(FakeStorage(), FakeStorage())
    await memory.async_initialize()
    await memory.async_add("alice", "Likes tea.", "preferences", "explicit")
    entity = FakeEntity(memory, MEMORY_RETRIEVAL_HYBRID, initial)
    sync_memory_embedding_provider(entity)
    assert await memory.async_prepare_hybrid(["alice"], "query")
    provider, space = memory._embedding_provider, memory._embedding_space_id
    before_cache = dict(memory._embedding_cache)
    before = memory.hybrid_status()
    entity.subentry.data[CONF_MEMORY_EMBEDDING_MODEL] = equivalent
    sync_memory_embedding_provider(entity)
    assert (
        memory._embedding_provider is provider and memory._embedding_space_id == space
    )
    assert memory.hybrid_status() == before and memory._embedding_cache == before_cache
    assert await memory.async_prepare_hybrid(["alice"], "query")
    assert [model for model, _ in entity.embedding_calls] == [expected] * 3


@pytest.mark.parametrize("change", ["model", "provider", "endpoint"])
async def test_genuine_configuration_changes_invalidate_old_space(change):
    memory = PersistentMemory(FakeStorage(), FakeStorage())
    await memory.async_initialize()
    await memory.async_add("alice", "Likes tea.", "preferences", "explicit")
    entity = FakeEntity(memory, MEMORY_RETRIEVAL_HYBRID, "model")
    sync_memory_embedding_provider(entity)
    await memory.async_prepare_hybrid(["alice"], "query")
    space = memory._embedding_space_id
    if change == "model":
        entity.subentry.data[CONF_MEMORY_EMBEDDING_MODEL] = "different-model"
    else:
        entity.entry.data[
            CONF_API_PROVIDER if change == "provider" else CONF_BASE_URL
        ] = "different"
    sync_memory_embedding_provider(entity)
    assert memory._embedding_space_id != space
    assert memory._cached_embedding(next(iter(memory._memories.values()))) is None
    await memory.async_prepare_hybrid(["alice"], "query")
    assert len(entity.embedding_calls) == 4
