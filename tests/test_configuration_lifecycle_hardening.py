"""Regression tests for live configuration/runtime synchronization."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    knowledge as knowledge_module,
)
from custom_components.extended_openai_conversation_responses import memory as memory_module
from custom_components.extended_openai_conversation_responses import (
    temporary_memory as temporary_memory_module,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_options,
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.configuration_lifecycle_hardening import (
    async_reconcile_runtime_configuration,
    install_configuration_lifecycle_hardening,
    sync_memory_embedding_provider,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_EMBEDDING_MODEL,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_TEMPORARY_MEMORY,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
    MEMORY_RETRIEVAL_HYBRID,
    MEMORY_RETRIEVAL_LEXICAL,
    TEMPORARY_MEMORY_BALANCED,
    TEMPORARY_MEMORY_OFF,
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


class StaleMemory:
    """Record provider clearing when persistent memory is disabled live."""

    def __init__(self) -> None:
        self.provider_calls: list[tuple[object | None, str]] = []

    def set_embedding_provider(self, provider, model: str) -> None:
        self.provider_calls.append((provider, model))


class RuntimeEntity:
    """Minimal long-lived agent shape for live-runtime reconciliation tests."""

    def __init__(self, data: dict) -> None:
        self.hass = SimpleNamespace()
        self.entry = SimpleNamespace(entry_id="entry")
        self.subentry = SimpleNamespace(subentry_id="agent", data=data)
        self._memory = None
        self._temporary_memory = None
        self._archive = None
        self._knowledge = None
        self._usage = SimpleNamespace(request_retention_days=0, run_retention_days=0)
        self._attr_supports_streaming = True
        self.archive_result = object()
        self.archive_init_calls: list[bool] = []
        self.statuses: list[tuple[str, bool, bool, type[Exception] | None]] = []

    async def _async_create_embeddings(self, _inputs: list[str]) -> list[list[float]]:
        return []

    async def _async_initialize_archive(self, configured: bool) -> None:
        self.archive_init_calls.append(configured)
        self._archive = self.archive_result

    def _set_subsystem_status(
        self,
        subsystem: str,
        configured: bool,
        error: Exception | None = None,
        *,
        healthy: bool = False,
    ) -> None:
        self.statuses.append(
            (subsystem, configured, healthy, type(error) if error is not None else None)
        )


def _disabled_runtime_config() -> dict:
    return {
        "memory_mode": MEMORY_MODE_OFF,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_OFF,
        CONF_ARCHIVE_ENABLED: False,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        CONF_KNOWLEDGE_ENABLED: False,
    }


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


@pytest.mark.asyncio
async def test_live_enable_disable_reconciles_optional_runtime_managers(monkeypatch) -> None:
    entity = RuntimeEntity(_disabled_runtime_config())
    stale_memory = StaleMemory()
    entity._memory = stale_memory
    entity._temporary_memory = object()
    entity._archive = object()

    await async_reconcile_runtime_configuration(entity, force=True)

    assert entity._memory is None
    assert entity._temporary_memory is None
    assert entity._archive is None
    assert stale_memory.provider_calls[-1][0] is None

    persistent = object()
    temporary = object()
    calls = {"memory": 0, "temporary": 0}

    async def get_memory(_hass, _entry_id, _subentry_id):
        calls["memory"] += 1
        return persistent

    async def get_temporary(_hass, _entry_id, _subentry_id):
        calls["temporary"] += 1
        return temporary

    monkeypatch.setattr(memory_module, "async_get_memory", get_memory)
    monkeypatch.setattr(
        temporary_memory_module, "async_get_temporary_memory", get_temporary
    )
    entity.subentry.data = {
        **_disabled_runtime_config(),
        "memory_mode": MEMORY_MODE_MANUAL,
        CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        CONF_ARCHIVE_ENABLED: True,
    }

    await async_reconcile_runtime_configuration(entity)

    assert entity._memory is persistent
    assert entity._temporary_memory is temporary
    assert entity._archive is entity.archive_result
    assert entity.archive_init_calls == [True]
    assert calls == {"memory": 1, "temporary": 1}

    # The same immutable-by-convention ConfigSubentry data mapping is the hot-path
    # revision marker, so unchanged requests do not reinitialize managers.
    await async_reconcile_runtime_configuration(entity)
    assert entity.archive_init_calls == [True]
    assert calls == {"memory": 1, "temporary": 1}

    entity.subentry.data = _disabled_runtime_config()
    await async_reconcile_runtime_configuration(entity)
    assert entity._memory is None
    assert entity._temporary_memory is None
    assert entity._archive is None


@pytest.mark.asyncio
async def test_failed_optional_manager_initialization_retries_next_request(monkeypatch) -> None:
    entity = RuntimeEntity(
        {
            **_disabled_runtime_config(),
            "memory_mode": MEMORY_MODE_MANUAL,
        }
    )
    persistent = object()
    calls = 0

    async def get_memory(_hass, _entry_id, _subentry_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient store failure")
        return persistent

    monkeypatch.setattr(memory_module, "async_get_memory", get_memory)

    await async_reconcile_runtime_configuration(entity)
    assert entity._memory is None
    assert calls == 1

    # No config edit is required: the failed state deliberately bypasses the normal
    # mapping-identity fast path until the required manager initializes successfully.
    await async_reconcile_runtime_configuration(entity)
    assert entity._memory is persistent
    assert calls == 2


@pytest.mark.asyncio
async def test_knowledge_initialization_failure_is_retryable(monkeypatch) -> None:
    entity = RuntimeEntity(
        {
            **_disabled_runtime_config(),
            CONF_KNOWLEDGE_ENABLED: True,
        }
    )
    knowledge = object()
    calls = 0

    async def get_knowledge(_hass, _entry_id, _subentry_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient store failure")
        return knowledge

    monkeypatch.setattr(knowledge_module, "async_get_knowledge", get_knowledge)

    await async_reconcile_runtime_configuration(entity)
    await async_reconcile_runtime_configuration(entity)

    assert entity._knowledge is knowledge
    assert calls == 2


@pytest.mark.asyncio
async def test_archive_search_enablement_initializes_runtime_without_retention() -> None:
    entity = RuntimeEntity(
        {
            **_disabled_runtime_config(),
            CONF_ARCHIVE_ENABLED: False,
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED: True,
        }
    )

    await async_reconcile_runtime_configuration(entity)

    assert entity._archive is entity.archive_result
    assert entity.archive_init_calls == [False]


@pytest.mark.asyncio
async def test_usage_retention_and_streaming_capability_follow_live_config() -> None:
    entity = RuntimeEntity(
        {
            **_disabled_runtime_config(),
            CONF_USAGE_REQUEST_RETENTION_DAYS: 7,
            CONF_USAGE_RUN_RETENTION_DAYS: 30,
            "speech_processing_enabled": True,
            "speech_regex_replacements": [
                {"pattern": "foo", "replacement": "bar"}
            ],
        }
    )

    await async_reconcile_runtime_configuration(entity)
    assert entity._usage.request_retention_days == 7
    assert entity._usage.run_retention_days == 30
    assert entity._attr_supports_streaming is False

    entity.subentry.data = {
        **_disabled_runtime_config(),
        CONF_USAGE_REQUEST_RETENTION_DAYS: 90,
        CONF_USAGE_RUN_RETENTION_DAYS: 180,
        "speech_processing_enabled": True,
        "speech_regex_replacements": [],
    }
    await async_reconcile_runtime_configuration(entity)

    assert entity._usage.request_retention_days == 90
    assert entity._usage.run_retention_days == 180
    assert entity._attr_supports_streaming is True


def test_streaming_property_reads_current_configuration_before_request_start() -> None:
    install_configuration_lifecycle_hardening()
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    descriptor = ExtendedOpenAIAgentEntity.__dict__["supports_streaming"]
    entity = SimpleNamespace(
        subentry=SimpleNamespace(
            data={
                "speech_processing_enabled": True,
                "speech_regex_replacements": [
                    {"pattern": "foo", "replacement": "bar"}
                ],
            }
        )
    )
    assert descriptor.fget(entity) is False

    entity.subentry.data = {
        "speech_processing_enabled": True,
        "speech_regex_replacements": [],
    }
    assert descriptor.fget(entity) is True
