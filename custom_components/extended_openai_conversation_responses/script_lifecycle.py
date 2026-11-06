"""Cleanup for transient Home Assistant Script runners."""

import asyncio
import logging
from typing import Any

from homeassistant.util.async_ import create_eager_task

from .operation_settlement import async_wait_owned_cleanup

_LOGGER = logging.getLogger(__name__)


async def async_run_script(script: Any, *args: Any, **kwargs: Any) -> Any:
    """Own the native run until it settles, including after caller cancellation."""
    # HA shields an internal run task. Cancelling its outer async_run await can
    # orphan a simultaneous native failure, even when async_stop/unload finishes.
    # Keep that await alive and request a normal native stop instead.
    run = create_eager_task(script.async_run(*args, **kwargs))
    try:
        await asyncio.wait({run})
        return run.result()
    except asyncio.CancelledError:

        async def stop_and_collect() -> None:
            try:
                if not run.done():
                    await script.async_stop()
            finally:
                await async_wait_owned_cleanup(run)
                try:
                    run.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _LOGGER.warning("Script execution failed during cancellation")

        stopping = create_eager_task(stop_and_collect())
        await async_wait_owned_cleanup(stopping)
        try:
            stopping.result()
        except asyncio.CancelledError, Exception:
            _LOGGER.warning("Script stop failed during cancellation")
        raise


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
