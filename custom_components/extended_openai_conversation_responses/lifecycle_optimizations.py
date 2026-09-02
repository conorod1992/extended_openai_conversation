"""Low-risk lifecycle and hot-path optimizations for conversation agents."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
_USAGE_SAVE_DELAY_SECONDS = 5.0
_LAST_USAGE_PRUNE_DATE = "_extended_openai_last_usage_prune_date"
_TEMPORARY_MEMORY_PREFETCH: ContextVar[asyncio.Task[Any] | None] = ContextVar(
    "extended_openai_temporary_memory_prefetch", default=None
)
_INSTALLED = False


def install_lifecycle_optimizations() -> None:
    """Install bounded persistence, archive, memory, and default optimizations."""
    global _INSTALLED
    if _INSTALLED:
        return

    _install_usage_persistence()
    _install_archive_fast_path()
    _install_memory_prefetch()
    _install_service_tier_default()
    _install_debug_summary_fields()
    _INSTALLED = True


def _usage_snapshot(manager: Any, category: str) -> dict[str, Any]:
    """Capture immutable usage state while the manager lock is held."""
    if category == "totals":
        return manager.as_dict()
    if category == "daily":
        return {"days": deepcopy(manager.daily)}
    if category == "details":
        return {
            "requests": [asdict(request) for request in manager.requests],
            "runs": [asdict(run) for run in manager.runs],
        }
    raise ValueError(f"Unknown usage persistence category: {category}")


def _schedule_store_snapshot(store: Any, snapshot: dict[str, Any]) -> bool:
    """Schedule a coalesced Store write when the persistence boundary supports it."""
    delay_save = getattr(store, "async_delay_save", None)
    if not callable(delay_save):
        return False
    delay_save(lambda snapshot=snapshot: snapshot, _USAGE_SAVE_DELAY_SECONDS)
    return True


def _prune_usage_locked(manager: Any) -> dict[str, int]:
    """Apply request/run retention while the UsageManager lock is held."""
    from .usage import _parse_time

    now = dt_util.utcnow()
    request_cutoff = now - timedelta(days=max(0, manager.request_retention_days))
    run_cutoff = now - timedelta(days=max(0, manager.run_retention_days))
    old_request_count = len(manager.requests)
    old_run_count = len(manager.runs)
    manager.requests = [
        request
        for request in manager.requests
        if manager.request_retention_days > 0
        and _parse_time(request.timestamp) >= request_cutoff
    ]
    manager.runs = [
        run
        for run in manager.runs
        if manager.run_retention_days > 0
        and _parse_time(run.started_at) >= run_cutoff
    ]
    return {
        "deleted_requests": old_request_count - len(manager.requests),
        "deleted_runs": old_run_count - len(manager.runs),
    }


async def _async_prune_usage_if_due(manager: Any) -> None:
    """Enforce detail retention at most once per UTC day during long uptimes."""
    today = dt_util.utcnow().date().isoformat()
    if getattr(manager, _LAST_USAGE_PRUNE_DATE, None) == today:
        return
    async with manager._lock:
        if getattr(manager, _LAST_USAGE_PRUNE_DATE, None) == today:
            return
        result = _prune_usage_locked(manager)
        setattr(manager, _LAST_USAGE_PRUNE_DATE, today)
        if (
            manager._detail_storage is not None
            and (result["deleted_requests"] or result["deleted_runs"])
        ):
            snapshot = _usage_snapshot(manager, "details")
            if not _schedule_store_snapshot(manager._detail_storage, snapshot):
                await manager._async_save_details()


def _install_usage_persistence() -> None:
    """Keep routine accounting in memory and coalesce Store writes off the hot path."""
    from .usage import UsageManager

    manager_type: Any = UsageManager
    original_save_safely = manager_type._async_save_safely
    original_finalize_run = manager_type._async_finalize_run

    async def async_save_safely(manager: Any, label: str, save: Any) -> None:
        category = {
            "request totals": "totals",
            "run totals": "totals",
            "daily run totals": "daily",
            "request details": "details",
            "run details": "details",
        }.get(label)
        store = {
            "totals": manager._storage,
            "daily": manager._daily_storage,
            "details": manager._detail_storage,
        }.get(category)
        if category is not None and store is not None:
            try:
                if _schedule_store_snapshot(store, _usage_snapshot(manager, category)):
                    return
            except Exception:
                _LOGGER.exception(
                    "Unable to schedule usage %s; falling back to immediate persistence",
                    label,
                )
        await original_save_safely(manager, label, save)

    async def async_finalize_run(manager: Any, run: Any) -> None:
        await original_finalize_run(manager, run)
        await _async_prune_usage_if_due(manager)

    async def async_prune_details(
        manager: Any, *, save: bool = True
    ) -> dict[str, int]:
        async with manager._lock:
            result = _prune_usage_locked(manager)
            setattr(
                manager,
                _LAST_USAGE_PRUNE_DATE,
                dt_util.utcnow().date().isoformat(),
            )
            if save and manager._detail_storage is not None:
                await manager._async_save_details()
            return result

    async def async_clear_details(
        manager: Any, *, confirm: bool
    ) -> dict[str, int]:
        if not confirm:
            raise ValueError("Explicit confirmation is required")
        async with manager._lock:
            result = {
                "deleted_requests": len(manager.requests),
                "deleted_runs": len(manager.runs),
            }
            manager.requests.clear()
            manager.runs.clear()
            if manager._detail_storage is not None:
                await manager._async_save_details()
            return result

    manager_type._async_save_safely = async_save_safely
    manager_type._async_finalize_run = async_finalize_run
    manager_type.async_prune_details = async_prune_details
    manager_type.async_clear_details = async_clear_details


def _install_archive_fast_path() -> None:
    """Do not initialize or touch archive storage for agents with archiving disabled."""
    from .conversation import ExtendedOpenAIAgentEntity

    agent_type: Any = ExtendedOpenAIAgentEntity
    original_initialize_archive = agent_type._async_initialize_archive

    async def async_initialize_archive(agent: Any, configured: bool) -> None:
        if not configured:
            agent._archive = None
            agent._set_subsystem_status("archive", False)
            return
        await original_initialize_archive(agent, configured)

    agent_type._async_initialize_archive = async_initialize_archive


def _install_memory_prefetch() -> None:
    """Overlap independent persistent- and temporary-memory retrieval."""
    from .conversation import ExtendedOpenAIAgentEntity

    agent_type: Any = ExtendedOpenAIAgentEntity
    original_retrieve_memories = agent_type._async_retrieve_memories
    original_retrieve_temporary = agent_type._async_retrieve_temporary_memories

    async def async_retrieve_memories(agent: Any, *args: Any, **kwargs: Any) -> Any:
        existing = _TEMPORARY_MEMORY_PREFETCH.get()
        task = existing
        if task is None:
            task = asyncio.create_task(original_retrieve_temporary(agent))
            _TEMPORARY_MEMORY_PREFETCH.set(task)
        try:
            return await original_retrieve_memories(agent, *args, **kwargs)
        except BaseException:
            if existing is None and task is not None:
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
                _TEMPORARY_MEMORY_PREFETCH.set(None)
            raise

    async def async_retrieve_temporary(
        agent: Any, *args: Any, **kwargs: Any
    ) -> Any:
        task = _TEMPORARY_MEMORY_PREFETCH.get()
        if task is None:
            return await original_retrieve_temporary(agent, *args, **kwargs)
        _TEMPORARY_MEMORY_PREFETCH.set(None)
        return await task

    agent_type._async_retrieve_memories = async_retrieve_memories
    agent_type._async_retrieve_temporary_memories = async_retrieve_temporary


def _install_service_tier_default() -> None:
    """Use the standard interactive provider tier when no tier was configured."""
    from . import agent_config, const, request

    standard_tier = "default"
    const.DEFAULT_SERVICE_TIER = standard_tier
    request.DEFAULT_SERVICE_TIER = standard_tier

    defaults = dict(agent_config.AGENT_CONFIG_DEFAULTS)
    defaults[const.CONF_SERVICE_TIER] = standard_tier
    agent_config.AGENT_CONFIG_DEFAULTS = MappingProxyType(defaults)

    # Config flow constants can be materialized before async_setup on an existing
    # installation. Keep those runtime defaults aligned without rewriting an
    # explicitly saved `flex` choice.
    try:
        from . import config_flow
    except ImportError:
        return
    config_flow.DEFAULT_SERVICE_TIER = standard_tier
    config_defaults = dict(config_flow.DEFAULT_OPTIONS)
    config_defaults[const.CONF_SERVICE_TIER] = standard_tier
    config_flow.DEFAULT_OPTIONS = MappingProxyType(config_defaults)


def _install_debug_summary_fields() -> None:
    """Expose enough continuity metadata to label debug rows unambiguously."""
    from .debug import DebugTrace

    trace_type: Any = DebugTrace
    original_summary = trace_type.summary

    def summary(trace: Any) -> dict[str, Any]:
        result = original_summary(trace)
        result["continuity_mode"] = trace.continuity.get("mode")
        result["restored_history_items"] = int(
            trace.continuity.get("restored_history_items") or 0
        )
        return result

    trace_type.summary = summary
