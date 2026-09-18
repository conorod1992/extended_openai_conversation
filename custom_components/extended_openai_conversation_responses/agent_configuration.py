"""Explicit helpers for agent configuration reconciliation and provider state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
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
_RUNTIME_CONFIG_DATA = "_extended_openai_runtime_config_data"
_RUNTIME_CONFIG_RETRY = "_extended_openai_runtime_config_retry"
_RUNTIME_CONFIG_LOCK = "_extended_openai_runtime_config_lock"


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
