"""Conversation-safe temporary-memory expiry maintenance."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .temporary_memory import TemporaryMemory, _parse_expiry

_LOGGER = logging.getLogger(__name__)
_INSTALLED = False
_PRUNE_SAVE_TASK = "_extended_openai_temporary_memory_prune_save_task"


def install_temporary_memory_read_fast_path() -> None:
    """Keep expiry filtering immediate while moving its Store write off reads."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    manager_type: Any = TemporaryMemory

    async def async_active(manager: Any, scope_id: str) -> list[Any]:
        expired_count = 0
        async with manager._lock:
            now = dt_util.utcnow()
            expired = [
                memory_id
                for memory_id, record in manager._records.items()
                if _parse_expiry(record.expires_at) <= now
            ]
            for memory_id in expired:
                del manager._records[memory_id]
            if expired:
                expired_count = len(expired)
                manager.expired_pruned += expired_count
            result = manager._active_snapshot_locked(scope_id)

        if expired_count:
            _schedule_pruned_state_save(manager)
        return result

    manager_type.async_active = async_active


def _schedule_pruned_state_save(manager: Any) -> None:
    """Persist an expiry-only mutation later through the transactional save seam."""
    current = getattr(manager, _PRUNE_SAVE_TASK, None)
    if isinstance(current, asyncio.Task) and not current.done():
        return

    async def persist() -> None:
        try:
            async with manager._lock:
                await manager._async_save_locked()
        except Exception:
            # Expired records remain invisible by timestamp even if persistence fails;
            # the existing transactional wrapper restores the last committed state.
            _LOGGER.exception("Unable to persist pruned temporary memories")

    task = asyncio.create_task(
        persist(), name="extended_openai_temporary_memory_expiry_persistence"
    )
    setattr(manager, _PRUNE_SAVE_TASK, task)

    def done(completed: asyncio.Task[Any]) -> None:
        if getattr(manager, _PRUNE_SAVE_TASK, None) is completed:
            setattr(manager, _PRUNE_SAVE_TASK, None)

    task.add_done_callback(done)
