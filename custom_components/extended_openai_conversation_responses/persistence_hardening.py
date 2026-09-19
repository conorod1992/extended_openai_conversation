"""Private Store preparation and residual delayed-tool persistence hardening."""

from __future__ import annotations

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
