"""Residual private Store permission repair for historical files."""

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
) -> None:
    """Settle a durable write before propagating cancellation or rollback."""
    save_task = asyncio.ensure_future(save)
    cancellation: asyncio.CancelledError | None = None

    # Once live manager state has changed, caller cancellation must not abandon an
    # in-flight Store write. Keep observing it to a known result, then preserve the
    # original rollback and cancellation precedence.
    while not save_task.done():
        try:
            await asyncio.shield(save_task)
        except asyncio.CancelledError as err:
            if save_task.cancelled():
                restore_committed_state()
                raise
            if cancellation is None:
                cancellation = err
        except Exception:
            # Inspect the finished task below so rollback and cancellation
            # precedence stay in one place.
            break

    try:
        save_task.result()
    except asyncio.CancelledError:
        restore_committed_state()
        raise
    except Exception as err:
        restore_committed_state()
        if cancellation is not None:
            raise cancellation from err
        raise

    remember_committed_state()
    if cancellation is not None:
        raise cancellation
