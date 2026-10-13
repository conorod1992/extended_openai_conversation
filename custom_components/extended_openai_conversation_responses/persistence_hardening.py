"""Private Store permission repair and durable generation settlement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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


async def _async_repair_private_store_mode(store: Any) -> None:
    """Tighten permissions on a historical Home Assistant Store before loading."""
    if not isinstance(store, Store):
        return
    await store.hass.async_add_executor_job(_repair_private_store_mode, store.path)


async def _async_settle_transactional_save(
    save: Awaitable[None],
    restore_committed_state: Callable[[], None],
    remember_committed_state: Callable[[], None],
    reconcile_failed_state: Callable[[], Awaitable[None]] | None,
    invalidate_state: Callable[[], None],
    *,
    record_execution: bool = True,
) -> None:
    """Settle a write and authoritative readback before reporting failure.

    Managers install their entire validated generation in reconcile_failed_state.
    Set record_execution=False when an enclosing operation owns the receipt;
    internal persistence must not start or settle that enclosing operation.
    """
    from .execution_outcomes import execution_completed, execution_started

    if record_execution:
        execution_started()
    cancellation: asyncio.CancelledError | None = None

    async def settle(operation: Awaitable[None]) -> None:
        nonlocal cancellation
        task = asyncio.ensure_future(operation)
        # The lock owner must observe both surviving writes and readbacks. A
        # second cancellation cannot abandon recovery or release the lock early.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as err:
                if task.cancelled():
                    break
                cancellation = cancellation or err
            except Exception:
                break
        task.result()

    try:
        await settle(save)
    except BaseException as err:
        if reconcile_failed_state is None:
            # Initialization repairs do not yet have a usable validator for
            # reloading. Preserve their established rollback/retry behavior.
            restore_committed_state()
        else:
            try:
                await settle(reconcile_failed_state())
            except BaseException:
                # A failed read cannot establish which generation reached disk.
                invalidate_state()
        if cancellation is not None:
            raise cancellation from err
        raise

    remember_committed_state()
    if record_execution:
        execution_completed()
    if cancellation is not None:
        raise cancellation
