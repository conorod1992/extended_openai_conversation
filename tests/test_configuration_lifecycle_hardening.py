"""Regression tests for live configuration/runtime synchronization."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_options,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.configuration_lifecycle_hardening import (
    install_configuration_lifecycle_hardening,
    sync_memory_embedding_provider,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_EMBEDDING_MODEL,
    CONF_MEMORY_RETRIEVAL_MODE,
    MEMORY_RETRIEVAL_HYBRID,
    MEMORY_RETRIEVAL_LEXICAL,
)
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory


class FakeStorage:
    """Detached in-memory storage for a PersistentMemory manager."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


class FakeEntity:
    """Minimal entity shape used by the embedding-provider synchronizer."""

    def __init__(self, memory: PersistentMemory, mode: str, model: str) -> None:
        self._memory = memory
        self.subentry = SimpleNamespace(
            data={
                CONF_MEMORY_RETRIEVAL_MODE: mode,
                CONF_MEMORY_EMBEDDING_MODEL: model,
            }
        )
        self.embedding_calls: list[tuple[str, list[str]]] = []

    async def _async_create_embeddings(self, inputs: list[str]) -> list[list[float]]:
        model = self.subentry.data[CONF_MEMORY_EMBEDDING_MODEL]
        self.embedding_calls.append((model, list(inputs)))
        return [[1.0, 0.0] for _ in inputs]


def test_custom_conversation_timeout_accepts_full_ui_range() -> None:
    install_configuration_lifecycle_hardening()

    assert normalize_agent_config({"conversation_timeout_minutes": 1})[
        "conversation_timeout_minutes"
    ] == 1
    assert normalize_agent_config({"conversation_timeout_minutes": 45})[
        "conversation_timeout_minutes"
    ] == 45
    assert normalize_agent_config({"conversation_timeout_minutes": 1440})[
        "conversation_timeout_minutes"
    ] == 1440
    assert normalize_agent_config({"conversation_timeout_minutes": "120"})[
        "conversation_timeout_minutes"
    ] == 120

    with pytest.raises(AgentConfigError):
        normalize_agent_config({"conversation_timeout_minutes": 0})
    with pytest.raises(AgentConfigError):
        normalize_agent_config({"conversation_timeout_minutes": 1441})


def test_custom_timeout_keeps_only_friendly_presets_in_option_metadata() -> None:
    install_configuration_lifecycle_hardening()

    assert [
        item["value"] for item in agent_config_options()["conversation_timeout_minutes"]
    ] == [5, 15, 30, 60, 240]


@pytest.mark.asyncio
async def test_live_memory_settings_replace_and_clear_shared_embedding_provider() -> None:
    install_configuration_lifecycle_hardening()
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")

    first = FakeEntity(memory, MEMORY_RETRIEVAL_HYBRID, "first-model")
    sync_memory_embedding_provider(first)
    assert memory._embedding_model == "first-model"
    assert await memory.async_prepare_hybrid(["alice"], "breed") == [1.0, 0.0]
    assert first.embedding_calls == [
        ("first-model", ["pets | Oscar is a Cavachon."]),
        ("first-model", ["breed"]),
    ]

    # A shared manager can outlive an entity instance. Lexical mode must clear the
    # provider rather than retaining a bound method from the previous entity.
    lexical = FakeEntity(memory, MEMORY_RETRIEVAL_LEXICAL, "first-model")
    sync_memory_embedding_provider(lexical)
    assert memory._embedding_provider is None
    assert await memory.async_prepare_hybrid(["alice"], "breed") is None

    # Enabling Hybrid again with a new model must bind the current entity and make
    # the old-model cache stale so it is regenerated before the query embedding.
    second = FakeEntity(memory, MEMORY_RETRIEVAL_HYBRID, "second-model")
    sync_memory_embedding_provider(second)
    assert memory._embedding_model == "second-model"
    assert await memory.async_prepare_hybrid(["alice"], "breed") == [1.0, 0.0]
    assert second.embedding_calls == [
        ("second-model", ["pets | Oscar is a Cavachon."]),
        ("second-model", ["breed"]),
    ]
    assert first.embedding_calls == [
        ("first-model", ["pets | Oscar is a Cavachon."]),
        ("first-model", ["breed"]),
    ]


def test_embedding_provider_sync_is_idempotent_for_unchanged_live_config() -> None:
    install_configuration_lifecycle_hardening()
    scheduled = []

    def scheduler(coroutine):
        scheduled.append(coroutine)
        coroutine.close()
        return SimpleNamespace(done=lambda: True)

    memory = PersistentMemory(FakeStorage(), embedding_task_scheduler=scheduler)
    memory._initialized = True
    entity = FakeEntity(memory, MEMORY_RETRIEVAL_HYBRID, "same-model")

    sync_memory_embedding_provider(entity)
    sync_memory_embedding_provider(entity)

    assert len(scheduled) == 1
