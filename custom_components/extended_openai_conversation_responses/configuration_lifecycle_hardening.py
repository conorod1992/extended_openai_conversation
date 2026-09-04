"""Keep live agent configuration and runtime helpers in sync.

This module closes narrow gaps where management already exposes valid live
configuration but long-lived conversation entities can retain startup-time state.
It keeps configuration changes cheap in the steady state while making optional
subsystems retryable after transient initialization failures.
"""

from __future__ import annotations

import asyncio
from functools import wraps
import logging
import re
from typing import Any

from . import agent_config, const
from .const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_EMBEDDING_MODEL,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_TEMPORARY_MEMORY,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
    DEFAULT_KNOWLEDGE_ENABLED,
    DEFAULT_MEMORY_EMBEDDING_MODEL,
    DEFAULT_MEMORY_RETRIEVAL_MODE,
    DEFAULT_TEMPORARY_MEMORY,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    MEMORY_RETRIEVAL_HYBRID,
    TEMPORARY_MEMORY_OFF,
)
from .memory import memory_enabled
from .speech import has_custom_speech_replacements

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_TIMEOUT_MINUTES_MIN = 1
_TIMEOUT_MINUTES_MAX = 1440
_INTEGER_STRING = re.compile(r"[+-]?\d+(?:\.0+)?")
_RUNTIME_CONFIG_DATA = "_extended_openai_runtime_config_data"
_RUNTIME_CONFIG_RETRY = "_extended_openai_runtime_config_retry"
_RUNTIME_CONFIG_LOCK = "_extended_openai_runtime_config_lock"


class _ConversationTimeoutOptions(list[int]):
    """Iterate presets while accepting the full supported custom range."""

    def __contains__(self, value: object) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and _TIMEOUT_MINUTES_MIN <= value <= _TIMEOUT_MINUTES_MAX
        )


def _install_conversation_timeout_validation() -> None:
    """Make backend validation match the UI's bounded Custom timeout control."""
    presets = list(const.CONVERSATION_TIMEOUT_OPTIONS)
    options = _ConversationTimeoutOptions(presets)
    # agent_config imported the constant by value, so update both module references.
    # Iteration still returns only the five friendly presets used by the frontend.
    const.CONVERSATION_TIMEOUT_OPTIONS = options
    agent_config.CONVERSATION_TIMEOUT_OPTIONS = options

    original_coerce = agent_config._coerce_legacy_numbers

    @wraps(original_coerce)
    def coerce_legacy_numbers(config: dict[str, Any]) -> None:
        original_coerce(config)
        value = config.get(CONF_CONVERSATION_TIMEOUT_MINUTES)
        if isinstance(value, str):
            stripped = value.strip()
            if _INTEGER_STRING.fullmatch(stripped):
                config[CONF_CONVERSATION_TIMEOUT_MINUTES] = int(
                    stripped.split(".", maxsplit=1)[0]
                )
        elif isinstance(value, float) and value.is_integer():
            config[CONF_CONVERSATION_TIMEOUT_MINUTES] = int(value)

    agent_config._coerce_legacy_numbers = coerce_legacy_numbers  # type: ignore[method-assign]


def sync_memory_embedding_provider(entity: Any) -> None:
    """Apply the entity's current Hybrid-memory settings to its shared manager."""
    memory = getattr(entity, "_memory", None)
    setter = getattr(memory, "set_embedding_provider", None)
    if not callable(setter):
        # Some callers/tests use a minimal search-only memory implementation. Provider
        # synchronization applies only to the real PersistentMemory capability.
        return
    options = entity.subentry.data
    model = str(
        options.get(CONF_MEMORY_EMBEDDING_MODEL, DEFAULT_MEMORY_EMBEDDING_MODEL)
    )
    if (
        options.get(CONF_MEMORY_RETRIEVAL_MODE, DEFAULT_MEMORY_RETRIEVAL_MODE)
        == MEMORY_RETRIEVAL_HYBRID
    ):
        setter(entity._async_create_embeddings, model)
    else:
        # The manager is shared per agent and can outlive one entity instance. Clear
        # a provider left by a previous Hybrid configuration/entity when Lexical is
        # now selected.
        setter(None, model)


def _set_subsystem_status(
    entity: Any,
    subsystem: str,
    configured: bool,
    error: Exception | None = None,
    *,
    healthy: bool = False,
) -> None:
    if getattr(entity, "hass", None) is None:
        return
    setter = getattr(entity, "_set_subsystem_status", None)
    if callable(setter):
        setter(subsystem, configured, error, healthy=healthy)


def _archive_runtime_required(options: Any) -> bool:
    return bool(
        options.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        or options.get(
            CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
            DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
        )
    )


def _gate_disabled_subsystems(entity: Any, options: Any) -> None:
    """Stop exposing disabled capabilities before any lazy initialization awaits."""
    if not memory_enabled(options):
        memory = getattr(entity, "_memory", None)
        setter = getattr(memory, "set_embedding_provider", None)
        if callable(setter):
            model = str(
                options.get(CONF_MEMORY_EMBEDDING_MODEL, DEFAULT_MEMORY_EMBEDDING_MODEL)
            )
            setter(None, model)
        entity._memory = None
        _set_subsystem_status(entity, "persistent_memory", False)

    temporary_enabled = (
        options.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
        != TEMPORARY_MEMORY_OFF
    )
    if not temporary_enabled:
        entity._temporary_memory = None
        _set_subsystem_status(entity, "temporary_memory", False)

    if not _archive_runtime_required(options):
        entity._archive = None
        _set_subsystem_status(entity, "archive", False)


def _refresh_non_manager_state(entity: Any, options: Any) -> None:
    """Refresh cheap live values kept on long-lived runtime objects."""
    entity._attr_supports_streaming = not has_custom_speech_replacements(options)
    usage = getattr(entity, "_usage", None)
    if usage is not None:
        usage.request_retention_days = int(
            options.get(
                CONF_USAGE_REQUEST_RETENTION_DAYS,
                DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
            )
        )
        usage.run_retention_days = int(
            options.get(CONF_USAGE_RUN_RETENTION_DAYS, DEFAULT_USAGE_RUN_RETENTION_DAYS)
        )


async def async_reconcile_runtime_configuration(
    entity: Any, *, force: bool = False
) -> None:
    """Make one long-lived agent match its current subentry configuration.

    Home Assistant replaces ``subentry.data`` when saved, so unchanged requests take
    the identity fast path. A failed required manager keeps the retry flag set, making
    the next request retry only the missing optional subsystem without an entity reload.
    """
    options = entity.subentry.data
    if (
        not force
        and getattr(entity, _RUNTIME_CONFIG_DATA, None) is options
        and not getattr(entity, _RUNTIME_CONFIG_RETRY, False)
    ):
        return

    # A few direct unit/service seams deliberately exercise the core request logic
    # on a partially constructed entity. Runtime manager reconciliation only makes
    # sense once Home Assistant has supplied the real entry identity.
    if (
        getattr(entity, "hass", None) is None
        or getattr(entity, "entry", None) is None
        or getattr(entity.subentry, "subentry_id", None) is None
    ):
        return

    lock = getattr(entity, _RUNTIME_CONFIG_LOCK, None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(entity, _RUNTIME_CONFIG_LOCK, lock)

    async with lock:
        options = entity.subentry.data
        _gate_disabled_subsystems(entity, options)
        _refresh_non_manager_state(entity, options)
        if (
            not force
            and getattr(entity, _RUNTIME_CONFIG_DATA, None) is options
            and not getattr(entity, _RUNTIME_CONFIG_RETRY, False)
        ):
            return

        from .conversation_archive import async_get_archive
        from .knowledge import async_get_knowledge
        from .memory import async_get_memory
        from .temporary_memory import async_get_temporary_memory

        retry = False
        entry_id = entity.entry.entry_id
        subentry_id = entity.subentry.subentry_id

        persistent_enabled = memory_enabled(options)
        if persistent_enabled and getattr(entity, "_memory", None) is None:
            try:
                entity._memory = await async_get_memory(
                    entity.hass, entry_id, subentry_id
                )
            except Exception as err:
                retry = True
                _set_subsystem_status(entity, "persistent_memory", True, err)
                _LOGGER.exception(
                    "Unable to initialize persistent memory after live "
                    "configuration change"
                )
        if persistent_enabled and getattr(entity, "_memory", None) is not None:
            _set_subsystem_status(entity, "persistent_memory", True, healthy=True)

        temporary_enabled = (
            options.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
            != TEMPORARY_MEMORY_OFF
        )
        if temporary_enabled and getattr(entity, "_temporary_memory", None) is None:
            try:
                entity._temporary_memory = await async_get_temporary_memory(
                    entity.hass, entry_id, subentry_id
                )
            except Exception as err:
                retry = True
                _set_subsystem_status(entity, "temporary_memory", True, err)
                _LOGGER.exception(
                    "Unable to initialize temporary memory after live "
                    "configuration change"
                )
        if temporary_enabled and getattr(entity, "_temporary_memory", None) is not None:
            _set_subsystem_status(entity, "temporary_memory", True, healthy=True)

        archive_enabled = bool(
            options.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        )
        archive_required = _archive_runtime_required(options)
        if archive_required and getattr(entity, "_archive", None) is None:
            initializer = getattr(entity, "_async_initialize_archive", None)
            if callable(initializer):
                await initializer(archive_enabled)
            else:
                try:
                    entity._archive = await async_get_archive(
                        entity.hass, entry_id, subentry_id
                    )
                except Exception as err:
                    _set_subsystem_status(entity, "archive", archive_enabled, err)
                    _LOGGER.exception(
                        "Unable to initialize conversation archive after live "
                        "configuration change"
                    )
            if getattr(entity, "_archive", None) is None:
                retry = True
        if archive_required and getattr(entity, "_archive", None) is not None:
            _set_subsystem_status(entity, "archive", archive_enabled, healthy=True)

        knowledge_enabled = bool(
            options.get(CONF_KNOWLEDGE_ENABLED, DEFAULT_KNOWLEDGE_ENABLED)
        )
        if knowledge_enabled and getattr(entity, "_knowledge", None) is None:
            try:
                entity._knowledge = await async_get_knowledge(
                    entity.hass, entry_id, subentry_id
                )
            except Exception as err:
                retry = True
                _set_subsystem_status(entity, "knowledge", True, err)
                _LOGGER.exception(
                    "Unable to initialize Knowledge Library after live "
                    "configuration change"
                )
        if knowledge_enabled and getattr(entity, "_knowledge", None) is not None:
            _set_subsystem_status(entity, "knowledge", True, healthy=True)
        elif not knowledge_enabled:
            _set_subsystem_status(entity, "knowledge", False)

        sync_memory_embedding_provider(entity)
        setattr(entity, _RUNTIME_CONFIG_DATA, options)
        setattr(entity, _RUNTIME_CONFIG_RETRY, retry)


def _install_memory_embedding_lifecycle() -> None:
    """Refresh Hybrid-memory provider state on startup and before each retrieval."""
    from .conversation import ExtendedOpenAIAgentEntity
    from .memory import PersistentMemory

    original_set_provider = PersistentMemory.set_embedding_provider

    @wraps(original_set_provider)
    def set_embedding_provider(
        memory: PersistentMemory, provider: Any, model: str = "default"
    ) -> None:
        # Re-reading unchanged live config happens on every automatic retrieval. Avoid
        # spawning redundant prewarm tasks while still replacing a bound method from
        # an older entity instance or a newly selected model.
        if memory._embedding_provider == provider and memory._embedding_model == model:
            return
        original_set_provider(memory, provider, model)
        if provider is None:
            memory._embedding_maintenance_requested = False

    PersistentMemory.set_embedding_provider = set_embedding_provider  # type: ignore[assignment]

    original_added = ExtendedOpenAIAgentEntity.async_added_to_hass

    @wraps(original_added)
    async def async_added_to_hass(entity: Any) -> None:
        await original_added(entity)
        # The base startup path enables Hybrid but historically did not clear a
        # provider retained by the shared manager when the new entity is Lexical.
        sync_memory_embedding_provider(entity)

    ExtendedOpenAIAgentEntity.async_added_to_hass = async_added_to_hass  # type: ignore[assignment]

    original_retrieve = ExtendedOpenAIAgentEntity._async_retrieve_memories

    @wraps(original_retrieve)
    async def async_retrieve_memories(entity: Any, *args: Any, **kwargs: Any) -> Any:
        # Management updates replace subentry.data without requiring an entity reload.
        # Reconcile the cheap provider/model pointer before the next retrieval so
        # Lexical <-> Hybrid and embedding-model changes take effect immediately.
        sync_memory_embedding_provider(entity)
        return await original_retrieve(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_retrieve_memories = async_retrieve_memories  # type: ignore[assignment]


def _install_runtime_configuration_lifecycle() -> None:
    """Reconcile optional runtime managers at request boundaries and live gates."""
    from .conversation import ExtendedOpenAIAgentEntity

    original_process = ExtendedOpenAIAgentEntity._async_process

    @wraps(original_process)
    async def async_process(entity: Any, *args: Any, **kwargs: Any) -> Any:
        await async_reconcile_runtime_configuration(entity)
        return await original_process(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_process = async_process  # type: ignore[assignment]

    original_memory_retrieve = ExtendedOpenAIAgentEntity._async_retrieve_memories

    @wraps(original_memory_retrieve)
    async def retrieve_memories(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if not memory_enabled(entity.subentry.data):
            return []
        return await original_memory_retrieve(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_retrieve_memories = retrieve_memories  # type: ignore[assignment]

    original_temporary_retrieve = (
        ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories
    )

    @wraps(original_temporary_retrieve)
    async def retrieve_temporary_memories(
        entity: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if (
            entity.subentry.data.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
            == TEMPORARY_MEMORY_OFF
        ):
            return []
        return await original_temporary_retrieve(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories = (  # type: ignore[assignment]
        retrieve_temporary_memories
    )

    original_memory_execute = ExtendedOpenAIAgentEntity._async_execute_memory_tool

    @wraps(original_memory_execute)
    async def execute_memory(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if not memory_enabled(entity.subentry.data):
            raise RuntimeError("persistent memory is disabled")
        return await original_memory_execute(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_execute_memory_tool = execute_memory  # type: ignore[assignment]

    original_temporary_execute = (
        ExtendedOpenAIAgentEntity._async_execute_temporary_memory_tool
    )

    @wraps(original_temporary_execute)
    async def execute_temporary_memory(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            entity.subentry.data.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
            == TEMPORARY_MEMORY_OFF
        ):
            raise RuntimeError("temporary memory is disabled")
        return await original_temporary_execute(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_execute_temporary_memory_tool = (  # type: ignore[assignment]
        execute_temporary_memory
    )

    original_archive_execute = ExtendedOpenAIAgentEntity._async_execute_archive_tool

    @wraps(original_archive_execute)
    async def execute_archive(entity: Any, *args: Any, **kwargs: Any) -> Any:
        if not _archive_runtime_required(entity.subentry.data):
            raise RuntimeError("conversation archive is disabled")
        return await original_archive_execute(entity, *args, **kwargs)

    ExtendedOpenAIAgentEntity._async_execute_archive_tool = execute_archive  # type: ignore[assignment]

    # Home Assistant queries this before entering async_process to decide whether to
    # attach its progressive listener, so derive it directly from live configuration
    # instead of relying only on the startup-time backing attribute.
    ExtendedOpenAIAgentEntity.supports_streaming = property(  # type: ignore[assignment]
        lambda entity: not has_custom_speech_replacements(entity.subentry.data)
    )


def install_configuration_lifecycle_hardening() -> None:
    """Install configuration/runtime synchronization once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_conversation_timeout_validation()
    _install_memory_embedding_lifecycle()
    _install_runtime_configuration_lifecycle()
    _INSTALLED = True