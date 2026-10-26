"""Cleanup for transient Home Assistant Script runners."""

import asyncio
import logging
from typing import Any

from .operation_settlement import async_wait_owned_cleanup

_LOGGER = logging.getLogger(__name__)


async def async_cleanup_script(
    script: Any,
    *,
    cancelled: bool = False,
    pending_stop: asyncio.Future[None] | None = None,
) -> None:
    """Settle one cleanup attempt before preserving or propagating cancellation."""

    async def cleanup() -> None:
        try:
            if pending_stop is not None:
                await pending_stop
        finally:
            if unload := getattr(script, "async_unload", None):
                await unload()
            else:
                await script.async_stop()

    # Native unload marks its runner unloaded before awaiting stop. Retrying or
    # abandoning that await can leave the registry and cached resources behind.
    task = asyncio.create_task(cleanup())
    cancellation = await async_wait_owned_cleanup(task)
    try:
        task.result()
    except asyncio.CancelledError as err:
        if not cancelled:
            raise cancellation or err from None
        _LOGGER.warning("Script cleanup was cancelled during cancellation")
    except Exception:
        if not cancelled and cancellation is None:
            raise
        # The secondary failure must remain observable without logging private
        # dependency prose or turning the cancelled execution into a tool error.
        _LOGGER.warning("Script cleanup failed during cancellation")
    if cancellation is not None and not cancelled:
        raise cancellation
