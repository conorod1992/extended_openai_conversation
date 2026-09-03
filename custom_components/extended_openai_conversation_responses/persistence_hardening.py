"""Transactional persistence guards for durable in-memory managers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from .archive_session_hardening import install_archive_session_hardening
from .context_usage_hardening import install_context_usage_hardening
from .hot_path_cleanup import install_hot_path_cleanup
from .knowledge import KnowledgeLibrary
from .lifecycle_optimizations import install_lifecycle_optimizations
from .memory import PersistentMemory
from .model_tool_results import install_model_tool_result_compaction
from .request_rules import DEFAULT_MATCHING, DEFAULT_WORDING_GROUPS, RequestRules
from .runtime_hardening import install_runtime_hardening
from .safety_hardening import install_safety_hardening
from .temporary_memory import TemporaryMemory
from .temporary_memory_performance import install_temporary_memory_read_fast_path

_COMMITTED_STATE = "_extended_openai_committed_state"
_INSTALLED: set[type[Any]] = set()


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
    install_runtime_hardening()
    install_safety_hardening()
    install_lifecycle_optimizations()
    install_archive_session_hardening()
    install_hot_path_cleanup()
    install_temporary_memory_read_fast_path()
    install_model_tool_result_compaction()
    install_context_usage_hardening()


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
