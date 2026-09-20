"""Residual private Store permission repair for historical files."""

from __future__ import annotations

import os
import stat
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


async def _async_repair_private_store_mode(store: Store[dict[str, Any]]) -> None:
    """Tighten permissions on a historical Store file before loading it."""
    await store.hass.async_add_executor_job(_repair_private_store_mode, store.path)
