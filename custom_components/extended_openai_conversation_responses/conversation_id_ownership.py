"""Ownership guard for caller-selected Home Assistant conversation IDs."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .scope import ResolvedDataScope

_CONVERSATION_ID_OWNERS = f"{DOMAIN}.conversation_id_owners"


def claim_conversation_id(
    hass: HomeAssistant | None,
    agent_id: str | None,
    scope: ResolvedDataScope,
    conversation_id: str | None,
    *,
    guest_active: bool,
) -> str | None:
    """Prevent a caller-selected HA ChatLog ID crossing EOAI privacy boundaries."""
    if (
        conversation_id is None
        or hass is None
        or not isinstance(agent_id, str)
        or not isinstance(getattr(hass, "data", None), dict)
    ):
        return conversation_id

    owners: dict[str, tuple[str, str]] = hass.data.setdefault(
        _CONVERSATION_ID_OWNERS, {}
    )
    owner = (agent_id, "guest" if guest_active else scope.scope_id)
    existing = owners.get(conversation_id)

    if existing is None:
        owners[conversation_id] = owner
        return conversation_id

    if existing == owner:
        owners.pop(conversation_id, None)
        owners[conversation_id] = owner
        return conversation_id

    claimed = f"extended-openai-{agent_id}-{uuid4().hex}"
    owners[claimed] = owner
    return claimed


def release_conversation_id_claim(
    hass: HomeAssistant | None,
    conversation_id: str,
    owner: tuple[str, str],
) -> None:
    """Drop one ownership claim only while it still belongs to this ChatLog."""
    hass_data = getattr(hass, "data", None)
    if not isinstance(hass_data, dict):
        return
    owners = hass_data.get(_CONVERSATION_ID_OWNERS)
    if isinstance(owners, dict) and owners.get(conversation_id) == owner:
        owners.pop(conversation_id, None)


def register_conversation_id_cleanup(
    session: Any,
    hass: HomeAssistant | None,
    conversation_id: str,
    owner: tuple[str, str],
) -> None:
    """Bind an ownership claim to a real HA ChatSession when available."""
    register = getattr(session, "async_on_cleanup", None)
    if not callable(register):
        return

    def release_claim() -> None:
        release_conversation_id_claim(hass, conversation_id, owner)

    register(release_claim)
