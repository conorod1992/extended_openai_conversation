"""Compatibility bootstrap for durable-state hardening.

Archive mutations and retention scheduling now belong to their owning classes.
"""

from __future__ import annotations

_INSTALLED = False


def install_durable_state_hardening() -> None:
    """Retain bootstrap compatibility until installer cleanup."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
