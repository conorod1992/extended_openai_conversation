"""Ownership guard for caller-selected Home Assistant conversation IDs."""

from __future__ import annotations

from typing import Any
from weakref import WeakValueDictionary

from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS, ChatLog
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .conversation_ids import GUEST_CONTINUITY_NAMESPACE, create_conversation_id
from .scope import ResolvedDataScope

_CONVERSATION_ID_OWNERS = f"{DOMAIN}.conversation_id_owners"
_UNCLAIMED_CHAT_LOGS = f"{DOMAIN}.unclaimed_chat_logs"


def conversation_id_is_owned(hass: HomeAssistant | None, conversation_id: str) -> bool:
    """Return whether an EOAI invocation has already claimed this history."""
    data = getattr(hass, "data", {})
    return isinstance(data, dict) and conversation_id in data.get(
        _CONVERSATION_ID_OWNERS, {}
    )


def retain_unclaimed_chat_log(source: ChatLog) -> None:
    """Preserve a foreign admission, excluding its live log from later EOAI use.

    A matching input alone does not prove that EOAI owns the caller's turn.
    Weak references expire with Core's logs; a replacement log with the same ID
    is a different history and must not inherit this exclusion.
    """
    logs: WeakValueDictionary[str, ChatLog] = source.hass.data.setdefault(
        _UNCLAIMED_CHAT_LOGS, WeakValueDictionary()
    )
    logs[source.conversation_id] = source


def _is_unclaimed_live_log(hass: HomeAssistant, conversation_id: str) -> bool:
    retained = hass.data.get(_UNCLAIMED_CHAT_LOGS, {}).get(conversation_id)
    return retained is not None and retained is hass.data.get(DATA_CHAT_LOGS, {}).get(
        conversation_id
    )


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
    owner = (agent_id, f"guest:{scope.scope_id}" if guest_active else scope.scope_id)
    existing = owners.get(conversation_id)

    if existing is None and not _is_unclaimed_live_log(hass, conversation_id):
        owners[conversation_id] = owner
        return conversation_id

    if existing == owner:
        owners.pop(conversation_id, None)
        owners[conversation_id] = owner
        return conversation_id

    claimed = create_conversation_id(
        agent_id, GUEST_CONTINUITY_NAMESPACE if guest_active else None
    )
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
