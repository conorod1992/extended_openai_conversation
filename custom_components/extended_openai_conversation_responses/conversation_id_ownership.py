"""Ownership guard for caller-selected Home Assistant conversation IDs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from weakref import ref

from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS, ChatLog
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .conversation_ids import GUEST_CONTINUITY_NAMESPACE, create_conversation_id
from .scope import ResolvedDataScope

_CONVERSATION_ID_OWNERS = f"{DOMAIN}.conversation_id_owners"
_UNCLAIMED_CHAT_LOGS = f"{DOMAIN}.unclaimed_chat_logs"
_CLEANUP_REGISTRATIONS = f"{DOMAIN}.conversation_id_cleanup"


def conversation_id_is_owned(hass: HomeAssistant | None, conversation_id: str) -> bool:
    """Return whether an EOAI invocation has already claimed this history."""
    data = getattr(hass, "data", {})
    return isinstance(data, dict) and conversation_id in data.get(
        _CONVERSATION_ID_OWNERS, {}
    )


@dataclass(slots=True)
class _UnclaimedHistory:
    source: ref[ChatLog]
    bindings: dict[tuple[str, str], str] = field(default_factory=dict)


def retain_unclaimed_chat_log(source: ChatLog) -> None:
    """Keep the parent foreign and bind EOAI continuations to its exact lifetime."""
    if conversation_id_is_owned(source.hass, source.conversation_id) or (
        source.hass.data.get(DATA_CHAT_LOGS, {}).get(source.conversation_id)
        is not source
    ):
        return
    histories: dict[str, _UnclaimedHistory] = source.hass.data.setdefault(
        _UNCLAIMED_CHAT_LOGS, {}
    )
    conversation_id = source.conversation_id
    existing = histories.get(conversation_id)
    if existing is not None and existing.source() is source:
        return

    def release(reference: ref[ChatLog]) -> None:
        current = histories.get(conversation_id)
        if current is not None and current.source is reference:
            histories.pop(conversation_id)

    histories[conversation_id] = _UnclaimedHistory(ref(source, release))


def _unclaimed_live_history(
    hass: HomeAssistant, conversation_id: str
) -> _UnclaimedHistory | None:
    histories: dict[str, _UnclaimedHistory] = hass.data.get(_UNCLAIMED_CHAT_LOGS, {})
    retained = histories.get(conversation_id)
    if (
        retained is not None
        and (source := retained.source()) is not None
        and source is hass.data.get(DATA_CHAT_LOGS, {}).get(conversation_id)
    ):
        return retained
    return None


def resolve_parent_conversation_id(
    hass: HomeAssistant | None,
    agent_id: str | None,
    scope: ResolvedDataScope,
    conversation_id: str | None,
    *,
    guest_active: bool,
) -> str | None:
    """Resolve an existing binding before continuity applies its Guest namespace."""
    if (
        hass is None
        or conversation_id is None
        or not isinstance(agent_id, str)
        or not isinstance(getattr(hass, "data", None), dict)
    ):
        return conversation_id
    if history := _unclaimed_live_history(hass, conversation_id):
        owner = (
            agent_id,
            f"guest:{scope.scope_id}" if guest_active else scope.scope_id,
        )
        target = history.bindings.get(owner)
        if (
            target is not None
            and hass.data.get(_CONVERSATION_ID_OWNERS, {}).get(target) == owner
        ):
            return target
    return conversation_id


def bind_parent_conversation_id(
    source: ChatLog | None, target: str, owner: tuple[str, str]
) -> None:
    """Retain the actual owned log for retries through an unchanged parent ID."""
    if source is not None and source.conversation_id != target:
        history = _unclaimed_live_history(source.hass, source.conversation_id)
        if (
            history is not None
            and source.hass.data.get(_CONVERSATION_ID_OWNERS, {}).get(target) == owner
        ):
            history.bindings[owner] = target


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
    history = _unclaimed_live_history(hass, conversation_id)
    if history is not None:
        target = history.bindings.get(owner)
        if target is not None and owners.get(target) == owner:
            return target
        target = create_conversation_id(
            agent_id, GUEST_CONTINUITY_NAMESPACE if guest_active else None
        )
        owners[target] = owner
        history.bindings[owner] = target
        return target

    if existing is None:
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


@dataclass(slots=True)
class _CleanupRegistration:
    session: Any
    owner: tuple[str, str]


def register_conversation_id_cleanup(
    session: Any,
    hass: HomeAssistant | None,
    conversation_id: str,
    owner: tuple[str, str],
) -> None:
    """Register once per session/owner and retire only that exact registration."""
    register = getattr(session, "async_on_cleanup", None)
    data = getattr(hass, "data", None)
    if not callable(register) or not isinstance(data, dict):
        return
    registrations: dict[str, _CleanupRegistration] = data.setdefault(
        _CLEANUP_REGISTRATIONS, {}
    )
    previous = registrations.get(conversation_id)
    if previous is not None and previous.session is session and previous.owner == owner:
        return
    registration = _CleanupRegistration(session, owner)
    registrations[conversation_id] = registration

    def release_claim() -> None:
        if (
            data.get(_CLEANUP_REGISTRATIONS) is registrations
            and registrations.get(conversation_id) is registration
        ):
            registrations.pop(conversation_id)
            release_conversation_id_claim(hass, conversation_id, owner)

    register(release_claim)
