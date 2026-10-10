"""Own the parent-entry conversation alias across sibling entity lifetimes."""

from __future__ import annotations

from typing import Any

from homeassistant.components import conversation
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_ALIASES = f"{DOMAIN}.legacy_agent_aliases"


def register_legacy_agent(hass: HomeAssistant, entry: Any, agent: Any) -> None:
    """Retain registration order so the most recently added sibling owns the alias."""
    siblings = hass.data.setdefault(_ALIASES, {}).setdefault(entry.entry_id, [])
    siblings[:] = [item for item in siblings if item is not agent]
    siblings.append(agent)
    conversation.async_set_agent(hass, entry, agent)


def unregister_legacy_agent(hass: HomeAssistant, entry: Any, agent: Any) -> None:
    """Removing a sibling must not remove the remaining siblings' shared alias."""
    aliases = hass.data.get(_ALIASES, {})
    siblings = aliases.get(entry.entry_id, [])
    was_owner = bool(siblings and siblings[-1] is agent)
    siblings[:] = [item for item in siblings if item is not agent]
    if siblings:
        if was_owner:
            conversation.async_set_agent(hass, entry, siblings[-1])
        return
    aliases.pop(entry.entry_id, None)
    if not aliases:
        hass.data.pop(_ALIASES, None)
    conversation.async_unset_agent(hass, entry)
