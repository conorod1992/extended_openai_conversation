"""Regression tests for live configuration/runtime synchronization."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    agent_configuration as hardening,
    const,
    conversation,
    conversation_archive,
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
from custom_components.extended_openai_conversation_responses.agent_configuration import (
    async_reconcile_runtime_configuration,
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


async def test_custom_conversation_timeout_accepts_full_ui_range(hass) -> None:

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

    assert [
        item["value"] for item in agent_config_options()["conversation_timeout_minutes"]
    ] == [5, 15, 30, 60, 240]


@pytest.mark.asyncio
async def test_live_memory_settings_replace_and_clear_shared_embedding_provider() -> None:
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
    memory = PersistentMemory(FakeStorage())
    memory._initialized = True
    entity = FakeEntity(memory, MEMORY_RETRIEVAL_HYBRID, "same-model")

    sync_memory_embedding_provider(entity)
    first_provider = memory._embedding_provider
    sync_memory_embedding_provider(entity)

    assert first_provider is not None
    assert memory._embedding_provider is first_provider
    assert memory._embedding_model == "same-model"


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


def _entity(options: dict) -> SimpleNamespace:
    statuses: list[tuple[str, bool, Exception | None, bool]] = []

    def set_status(
        subsystem: str,
        configured: bool,
        error: Exception | None = None,
        *,
        healthy: bool = False,
    ) -> None:
        statuses.append((subsystem, configured, error, healthy))

    return SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent", data=options),
        _memory=None,
        _temporary_memory=None,
        _archive=None,
        _knowledge=None,
        _usage=None,
        _set_subsystem_status=set_status,
        _async_create_embeddings=AsyncMock(),
        statuses=statuses,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", "12.5"),
        ("abc", "abc"),
        (12.0, 12),
    ],
)
def test_timeout_coercion_handles_invalid_strings_and_integral_float(
    monkeypatch, raw, expected
) -> None:
    config = {const.CONF_CONVERSATION_TIMEOUT_MINUTES: raw}
    agent_config._coerce_legacy_numbers(config)

    assert config[const.CONF_CONVERSATION_TIMEOUT_MINUTES] == expected


@pytest.mark.parametrize("hass_value", [None, object()])
def test_set_subsystem_status_noops_without_callable_setter(hass_value) -> None:
    sentinel = object()
    entity = SimpleNamespace(hass=hass_value, _set_subsystem_status=sentinel)

    hardening._set_subsystem_status(entity, "archive", True)

    assert entity._set_subsystem_status is sentinel


@pytest.mark.asyncio
async def test_runtime_refresh_returns_when_configuration_identity_is_unchanged(
    monkeypatch,
) -> None:
    options: dict = {}
    entity = _entity(options)
    setattr(entity, hardening._RUNTIME_CONFIG_DATA, options)
    setattr(entity, hardening._RUNTIME_CONFIG_RETRY, False)

    monkeypatch.setattr(
        hardening,
        "_gate_disabled_subsystems",
        lambda *_args: pytest.fail("outer identity fast path should return first"),
    )

    await hardening.async_reconcile_runtime_configuration(entity)


@pytest.mark.asyncio
async def test_temporary_memory_initialization_failure_requests_retry(
    monkeypatch, caplog
) -> None:
    options = {
        const.CONF_TEMPORARY_MEMORY: "enabled",
        const.CONF_ARCHIVE_ENABLED: False,
        const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        const.CONF_KNOWLEDGE_ENABLED: False,
    }
    entity = _entity(options)
    failure = RuntimeError("temporary store unavailable")
    get_temporary = AsyncMock(side_effect=failure)
    monkeypatch.setattr(temporary_memory_module, "async_get_temporary_memory", get_temporary)
    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)

    await hardening.async_reconcile_runtime_configuration(entity, force=True)

    assert entity._temporary_memory is None
    assert getattr(entity, hardening._RUNTIME_CONFIG_RETRY) is True
    assert any(
        subsystem == "temporary_memory" and configured and error is failure
        for subsystem, configured, error, _healthy in entity.statuses
    )
    assert "Unable to initialize temporary memory" in caplog.text
    get_temporary.assert_awaited_once_with(entity.hass, "entry", "agent")


@pytest.mark.asyncio
async def test_archive_get_failure_requests_retry_and_records_status(
    monkeypatch, caplog
) -> None:
    options = {
        const.CONF_TEMPORARY_MEMORY: const.TEMPORARY_MEMORY_OFF,
        const.CONF_ARCHIVE_ENABLED: True,
        const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        const.CONF_KNOWLEDGE_ENABLED: False,
    }
    entity = _entity(options)
    failure = RuntimeError("archive unavailable")
    get_archive = AsyncMock(side_effect=failure)
    monkeypatch.setattr(conversation_archive, "async_get_archive", get_archive)
    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)

    await hardening.async_reconcile_runtime_configuration(entity, force=True)

    assert entity._archive is None
    assert getattr(entity, hardening._RUNTIME_CONFIG_RETRY) is True
    assert any(
        subsystem == "archive" and configured and error is failure
        for subsystem, configured, error, _healthy in entity.statuses
    )
    assert "Unable to initialize conversation archive" in caplog.text
    get_archive.assert_awaited_once_with(entity.hass, "entry", "agent")


@pytest.mark.asyncio
async def test_archive_initializer_leaving_runtime_missing_requests_retry(
    monkeypatch,
) -> None:
    options = {
        const.CONF_TEMPORARY_MEMORY: const.TEMPORARY_MEMORY_OFF,
        const.CONF_ARCHIVE_ENABLED: True,
        const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        const.CONF_KNOWLEDGE_ENABLED: False,
    }
    entity = _entity(options)
    initializer = AsyncMock()
    entity._async_initialize_archive = initializer
    monkeypatch.setattr(hardening, "memory_enabled", lambda _options: False)

    await hardening.async_reconcile_runtime_configuration(entity, force=True)

    initializer.assert_awaited_once_with(True)
    assert entity._archive is None
    assert getattr(entity, hardening._RUNTIME_CONFIG_RETRY) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "options", "message"),
    [
        (
            "_async_execute_memory_tool",
            {},
            "persistent memory is disabled",
        ),
        (
            "_async_execute_temporary_memory_tool",
            {const.CONF_TEMPORARY_MEMORY: const.TEMPORARY_MEMORY_OFF},
            "temporary memory is disabled",
        ),
        (
            "_async_execute_archive_tool",
            {
                const.CONF_ARCHIVE_ENABLED: False,
                const.CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
            },
            "conversation archive is disabled",
        ),
    ],
)
async def test_disabled_tool_guards_block_original_execution(
    monkeypatch, tool_name: str, options: dict, message: str
) -> None:
    entity = object.__new__(conversation.ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(data=options)
    monkeypatch.setattr(conversation, "memory_enabled", lambda _options: False)
    scope = conversation._ACTIVE_SCOPE.set(
        SimpleNamespace(scope_type="user", user_id="alice")
    )
    temporary = conversation._ACTIVE_TEMPORARY_SCOPE.set("session")
    try:
        method = getattr(entity, tool_name)
        args = (
            ("list", {}, None)
            if tool_name == "_async_execute_memory_tool"
            else ("list", {})
        )
        with pytest.raises(RuntimeError, match=message):
            await method(*args)
    finally:
        conversation._ACTIVE_SCOPE.reset(scope)
        conversation._ACTIVE_TEMPORARY_SCOPE.reset(temporary)
