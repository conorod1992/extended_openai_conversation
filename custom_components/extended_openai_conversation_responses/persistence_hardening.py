"""Private Store preparation and residual delayed-tool persistence hardening."""

from __future__ import annotations

from dataclasses import replace
import os
import stat
from typing import Any

from homeassistant.helpers.storage import Store

_PRIVATE_STORE_MODE = 0o600


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


def install_delayed_tool_store_guard() -> None:
    """Harden delayed-tool persistence and keep retry limits fail-safe."""
    from .delayed_tools import _MAX_AGENT_RETRIES, DelayedToolManager

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
