"""Transactional persistence guards for durable in-memory managers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
import os
import stat
from typing import Any

from homeassistant.helpers.storage import Store

from .configuration_lifecycle_hardening import install_configuration_lifecycle_hardening
from .context_usage_hardening import install_context_usage_hardening
from .durable_state_hardening import install_durable_state_hardening
from .hot_path_cleanup import install_hot_path_cleanup
from .knowledge import KnowledgeLibrary
from .lifecycle_optimizations import install_lifecycle_optimizations
from .memory import PersistentMemory
from .model_tool_results import install_model_tool_result_compaction
from .request_rules import DEFAULT_MATCHING, DEFAULT_WORDING_GROUPS, RequestRules
from .runtime_failure_hardening import install_runtime_failure_hardening
from .runtime_hardening import install_runtime_hardening
from .safety_hardening import install_safety_hardening
from .temporary_memory import TemporaryMemory
from .temporary_memory_performance import install_temporary_memory_read_fast_path

_COMMITTED_STATE = "_extended_openai_committed_state"
_INSTALLED: set[type[Any]] = set()
_PRIVATE_STORE_MODE = 0o600


type Snapshotter = Callable[[Any], Any]
type Restorer = Callable[[Any, Any], None]
type Resetter = Callable[[Any], None]


def install_persistence_transactions() -> None:
    """Make durable manager mutations roll back when their Store write fails."""
    _install_manager_guard(
        PersistentMemory,
        _snapshot_memory,
        _restore_memory,
        _reset_memory,
    )
    _install_manager_guard(
        KnowledgeLibrary,
        _snapshot_knowledge,
        _restore_knowledge,
        _reset_knowledge,
    )
    _install_manager_guard(
        TemporaryMemory,
        _snapshot_temporary_memory,
        _restore_temporary_memory,
        _reset_temporary_memory,
    )
    _install_manager_guard(
        RequestRules,
        _snapshot_request_rules,
        _restore_request_rules,
        _reset_request_rules,
    )
    _install_delayed_tool_store_guard()
    install_configuration_lifecycle_hardening()
    install_runtime_failure_hardening()
    install_runtime_hardening()
    install_safety_hardening()
    install_lifecycle_optimizations()
    install_hot_path_cleanup()
    install_temporary_memory_read_fast_path()
    install_model_tool_result_compaction()
    install_context_usage_hardening()
    # This must remain after lifecycle/hot-path installers because those layers
    # replace Usage pruning. The final wrapper restores persist-before-publish
    # semantics without moving daily retention back onto the response path.
    install_durable_state_hardening()


def _repair_private_store_mode(path: str) -> None:
    """Tighten one existing Store file without rewriting unchanged JSON."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return
    if mode != _PRIVATE_STORE_MODE:
        os.chmod(path, _PRIVATE_STORE_MODE)


async def _async_prepare_private_store(store: Any) -> None:
    """Enable private atomic writes and repair an existing Store before loading."""
    if not isinstance(store, Store):
        return
    # Store has no public setters for these constructor options. These managers
    # predate the flags, so harden their existing Store instances before first I/O.
    store._private = True
    store._atomic_writes = True
    await store.hass.async_add_executor_job(_repair_private_store_mode, store.path)


def _install_delayed_tool_store_guard() -> None:
    """Harden delayed-tool persistence and keep retry limits fail-safe."""
    from .delayed_tools import DelayedToolManager, _MAX_AGENT_RETRIES

    current_setup = DelayedToolManager.async_setup
    if not getattr(current_setup, "_extended_openai_private_store", False):
        original_setup = current_setup

        async def async_setup(manager: Any) -> None:
            await _async_prepare_private_store(manager._store)
            await original_setup(manager)

        async_setup._extended_openai_private_store = True  # type: ignore[attr-defined]
        setattr(  # noqa: B010
            DelayedToolManager,
            "async_setup",
            async_setup,
        )

    current_retry = DelayedToolManager._async_retry_agent
    if getattr(current_retry, "_extended_openai_retry_budget", False):
        return
    original_retry = current_retry

    async def async_retry_agent(manager: Any, record: Any) -> bool:
        retry = await original_retry(manager, record)
        if not retry or record.retry_count >= _MAX_AGENT_RETRIES:
            return retry

        # retry_count is safety bookkeeping rather than an execution boundary. If
        # its Store write failed, the original method deliberately left RAM at the
        # persisted state; advance only this live budget so a storage outage cannot
        # create unbounded agent-resolution retries. A later successful write starts
        # from this live count and catches durable state up automatically.
        current_record = manager._records.get(record.call_id)
        if (
            current_record is not None
            and current_record.status == record.status
            and current_record.retry_count == record.retry_count
        ):
            manager._records[record.call_id] = replace(
                record,
                retry_count=record.retry_count + 1,
            )
        return retry

    async_retry_agent._extended_openai_retry_budget = True  # type: ignore[attr-defined]
    setattr(  # noqa: B010
        DelayedToolManager,
        "_async_retry_agent",
        async_retry_agent,
    )


def _install_manager_guard(
    manager_type: type[Any],
    snapshotter: Snapshotter,
    restorer: Restorer,
    resetter: Resetter,
) -> None:
    """Wrap initialization and the save boundary once for one manager class."""
    if manager_type in _INSTALLED:
        return

    original_initialize: Callable[..., Awaitable[Any]] = manager_type.async_initialize
    original_save: Callable[..., Awaitable[Any]] = manager_type._async_save_locked

    async def async_initialize(manager: Any, *args: Any, **kwargs: Any) -> Any:
        if manager_type is RequestRules:
            await _async_prepare_private_store(manager._store)
        try:
            result = await original_initialize(manager, *args, **kwargs)
        except Exception:
            # Initialization can itself rewrite migrated/pruned data. A failed write
            # must leave the manager retryable instead of exposing half-loaded state.
            resetter(manager)
            raise
        setattr(manager, _COMMITTED_STATE, snapshotter(manager))
        return result

    async def async_save_locked(manager: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            result = await original_save(manager, *args, **kwargs)
        except Exception:
            committed = getattr(manager, _COMMITTED_STATE, None)
            if committed is not None:
                restorer(manager, committed)
            raise
        setattr(manager, _COMMITTED_STATE, snapshotter(manager))
        return result

    manager_type.async_initialize = async_initialize
    manager_type._async_save_locked = async_save_locked
    _INSTALLED.add(manager_type)


def _snapshot_memory(manager: Any) -> dict[str, Any]:
    """Keep only durable facts/counter state; derived indexes are rebuilt if needed."""
    return {"memories": dict(manager._memories)}


def _restore_memory(manager: Any, snapshot: dict[str, Any]) -> None:
    manager._memories = dict(snapshot["memories"])
    manager._token_index = defaultdict(set)
    manager._key_index = {}
    for memory in manager._memories.values():
        manager._index(memory)
    # Embeddings are a regenerable cache. Clearing them is safer than retaining an
    # entry invalidated by the failed mutation while restoring an older fact.
    manager._embedding_cache.clear()
    manager._embedding_cache_dirty = True


def _reset_memory(manager: Any) -> None:
    manager._memories.clear()
    manager._token_index = defaultdict(set)
    manager._key_index.clear()
    manager._embedding_cache.clear()
    manager._embedding_cache_dirty = False
    manager._initialized = False
    if hasattr(manager, _COMMITTED_STATE):
        delattr(manager, _COMMITTED_STATE)


def _snapshot_knowledge(manager: Any) -> dict[str, Any]:
    return {"sources": dict(manager._sources)}


def _restore_knowledge(manager: Any, snapshot: dict[str, Any]) -> None:
    manager._sources = dict(snapshot["sources"])
    manager._chunks.clear()
    manager._token_index = defaultdict(set)
    for source in manager._sources.values():
        manager._index(source)


def _reset_knowledge(manager: Any) -> None:
    manager._sources.clear()
    manager._chunks.clear()
    manager._token_index = defaultdict(set)
    manager._initialized = False
    if hasattr(manager, _COMMITTED_STATE):
        delattr(manager, _COMMITTED_STATE)


def _snapshot_temporary_memory(manager: Any) -> dict[str, Any]:
    return {
        "records": dict(manager._records),
        "expired_pruned": manager.expired_pruned,
    }


def _restore_temporary_memory(manager: Any, snapshot: dict[str, Any]) -> None:
    manager._records = dict(snapshot["records"])
    manager.expired_pruned = int(snapshot["expired_pruned"])


def _reset_temporary_memory(manager: Any) -> None:
    manager._records.clear()
    manager.expired_pruned = 0
    manager._initialized = False
    if hasattr(manager, _COMMITTED_STATE):
        delattr(manager, _COMMITTED_STATE)


def _snapshot_request_rules(manager: Any) -> dict[str, Any]:
    """Capture the exact last committed Request Rule configuration."""
    return {
        "defaults": deepcopy(manager._defaults),
        "wording_groups": deepcopy(manager._wording_groups),
        "rules": deepcopy(manager._rules),
    }


def _restore_request_rules(manager: Any, snapshot: dict[str, Any]) -> None:
    manager._defaults = deepcopy(snapshot["defaults"])
    manager._wording_groups = deepcopy(snapshot["wording_groups"])
    manager._rules = deepcopy(snapshot["rules"])
    manager._sort_and_compile()


def _reset_request_rules(manager: Any) -> None:
    manager._defaults = dict(DEFAULT_MATCHING)
    manager._wording_groups = deepcopy(list(DEFAULT_WORDING_GROUPS))
    manager._rules = []
    manager._compiled = []
    manager._initialized = False
    if hasattr(manager, _COMMITTED_STATE):
        delattr(manager, _COMMITTED_STATE)
