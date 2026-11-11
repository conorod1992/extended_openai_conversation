"""Loaded-entry ownership for integration-global automation and Broadcast."""

from __future__ import annotations

import asyncio
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

LOADED_ENTRIES = f"{DOMAIN}.loaded_entries"
_LOCK = f"{DOMAIN}.global_runtime_lock"


def initialize_global_runtime(hass: HomeAssistant) -> set[str]:
    """Establish suspension before setup exposes any public entry points."""
    return cast(set[str], hass.data.setdefault(LOADED_ENTRIES, set()))


def global_runtime_available(hass: HomeAssistant) -> bool:
    # Before integration setup this module also serves standalone backend users.
    # Real HA setup always establishes explicit ownership before registering APIs.
    data = getattr(hass, "data", {})
    return data.get(f"{DOMAIN}.removed") is not True and (
        LOADED_ENTRIES not in data or bool(data[LOADED_ENTRIES])
    )


def require_global_runtime(hass: HomeAssistant) -> None:
    if not global_runtime_available(hass):
        raise HomeAssistantError(
            "Extended OpenAI global features require a successfully loaded entry"
        )


def global_runtime_lock(hass: HomeAssistant) -> asyncio.Lock:
    return cast(asyncio.Lock, hass.data.setdefault(_LOCK, asyncio.Lock()))
