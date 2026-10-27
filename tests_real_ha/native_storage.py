"""Opt-in native disk I/O from the first Core Store's construction."""

from typing import Any

import pytest


@pytest.fixture
def hass_storage() -> dict[str, Any]:
    """Keep Core's in-memory storage shim out of dedicated physical-file tests.

    Import this fixture explicitly in tests that seed and inspect real files.
    Newer Core Stores retain their I/O backend at construction; undoing the shim
    after the hass fixture starts leaves early and fresh Stores on different
    backends. An empty fixture inventory preserves a fresh instance without
    installing that shim in the first place.
    """
    return {}
