"""Cleanup for transient Home Assistant Script runners."""

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


async def async_cleanup_script(script: Any, *, cancelled: bool = False) -> None:
    """Attempt cleanup without replacing a cancellation already unwinding."""
    try:
        if unload := getattr(script, "async_unload", None):
            await unload()
        else:
            await script.async_stop()
    except Exception:
        if not cancelled:
            raise
        # The secondary failure must remain observable without logging private
        # dependency prose or turning the cancelled execution into a tool error.
        _LOGGER.warning("Script cleanup failed during cancellation")
