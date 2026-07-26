"""Diagnostics for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
    DEFAULT_MEMORY_AUTO_CREATE,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_MEMORY_ENABLED,
)
from .memory import async_get_memory


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive persistent-memory diagnostics."""
    agents: list[dict[str, Any]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        diagnostics: dict[str, Any] = {
            "subentry_id": subentry.subentry_id,
            "memory_enabled": subentry.data.get(
                CONF_MEMORY_ENABLED, DEFAULT_MEMORY_ENABLED
            ),
            "automatic_memory_creation": subentry.data.get(
                CONF_MEMORY_AUTO_CREATE, DEFAULT_MEMORY_AUTO_CREATE
            ),
            "automatic_retrieval_limit": subentry.data.get(
                CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
                DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
            ),
        }
        try:
            memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
            diagnostics.update(memory.stats())
        except Exception as err:
            diagnostics["storage_error"] = type(err).__name__
        agents.append(diagnostics)
    return {"conversation_agents": agents}
