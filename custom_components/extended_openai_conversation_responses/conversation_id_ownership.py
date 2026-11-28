"""Ownership guard for caller-selected Home Assistant conversation IDs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from weakref import ref

from homeassistant.components.conversation.chat_log import (
    DATA_CHAT_LOGS,
    ChatLog,
    SystemContent,
)
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.chat_session import (
    CONVERSATION_TIMEOUT,
    DATA_CHAT_SESSION,
    ChatSession,
)
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .conversation_ids import (
    GUEST_CONTINUITY_NAMESPACE,
    GUEST_CONVERSATION_ID_PREFIX,
    create_conversation_id,
)
from .scope import ResolvedDataScope

_CONVERSATION_ID_OWNERS = f"{DOMAIN}.conversation_id_owners"
_UNCLAIMED_CHAT_LOGS = f"{DOMAIN}.unclaimed_chat_logs"
_CLEANUP_REGISTRATIONS = f"{DOMAIN}.conversation_id_cleanup"


@dataclass(slots=True)
class ProvisionalConversationIDClaim:
    """Attribute a newly inserted mapping to its invocation until attachment."""

    hass: HomeAssistant | None
    conversation_id: str | None = None
    owner: tuple[str, str] | None = None
    newly_created: bool = False

    def record(
        self, conversation_id: str, owner: tuple[str, str], *, created: bool
    ) -> str:
        """Record the returned ID, which may differ from the supplied ID."""
        self.conversation_id = conversation_id
        self.owner = owner
        self.newly_created = created
        return conversation_id

    def release_unused(self) -> None:
        """Retire only a new claim lacking an authorized session attachment.

        Reused mappings can protect retained history outside Core's log cache.
        A matching cleanup registration proves attachment even if later ChatLog
        admission or processing failed. Native cleanup retains its identity guard.
        """
        if not self.newly_created or self.conversation_id is None or self.owner is None:
            return
        data = getattr(self.hass, "data", {})
        registration = data.get(_CLEANUP_REGISTRATIONS, {}).get(self.conversation_id)
        if registration is None or registration.owner != self.owner:
            release_conversation_id_claim(self.hass, self.conversation_id, self.owner)


def conversation_id_is_owned(hass: HomeAssistant | None, conversation_id: str) -> bool:
    """Return whether an EOAI invocation has already claimed this history."""
    data = getattr(hass, "data", {})
    return isinstance(data, dict) and conversation_id in data.get(
        _CONVERSATION_ID_OWNERS, {}
    )


def has_live_conversation_owner(
    hass: HomeAssistant,
    agent_id: str,
    conversation_id: str,
    scope_id: str,
    *,
    require_recent_activity: bool = True,
) -> bool:
    """Check that a retained Archive binding still owns the active native chat."""
    if conversation_id.startswith(GUEST_CONVERSATION_ID_PREFIX):
        return False
    owner = (agent_id, scope_id)
    session = hass.data.get(DATA_CHAT_SESSION, {}).get(conversation_id)
    registration = hass.data.get(_CLEANUP_REGISTRATIONS, {}).get(conversation_id)
    return (
        hass.data.get(_CONVERSATION_ID_OWNERS, {}).get(conversation_id) == owner
        and session is not None
        and registration is not None
        and registration.session is session
        and registration.owner == owner
        and conversation_id in hass.data.get(DATA_CHAT_LOGS, {})
        and (
            not require_recent_activity
            or session.last_updated + CONVERSATION_TIMEOUT >= dt_util.utcnow()
        )
    )


@dataclass(slots=True)
class _UnclaimedHistory:
    source: ref[ChatLog]
    bindings: dict[tuple[str, str], str] = field(default_factory=dict)


def retain_unclaimed_chat_log(source: ChatLog) -> None:
    """Keep the parent foreign and bind EOAI continuations to its exact lifetime."""
    if source.hass.data.get(DATA_CHAT_LOGS, {}).get(source.conversation_id) is source:
        _unclaimed_live_history(source.hass, source.conversation_id)


def _unclaimed_live_history(
    hass: HomeAssistant, conversation_id: str
) -> _UnclaimedHistory | None:
    """Discover unowned logs even when the caller has no active ChatLog."""
    if conversation_id_is_owned(hass, conversation_id):
        return None
    source = hass.data.get(DATA_CHAT_LOGS, {}).get(conversation_id)
    if source is None:
        return None
    histories: dict[str, _UnclaimedHistory] = hass.data.setdefault(
        _UNCLAIMED_CHAT_LOGS, {}
    )
    existing = histories.get(conversation_id)
    if existing is not None and existing.source() is source:
        return existing

    def release(reference: ref[ChatLog]) -> None:
        current = histories.get(conversation_id)
        if current is not None and current.source is reference:
            histories.pop(conversation_id)

    history = _UnclaimedHistory(ref(source, release))
    histories[conversation_id] = history
    return history


def resolve_parent_conversation_id(
    hass: HomeAssistant | None,
    agent_id: str | None,
    scope: ResolvedDataScope,
    conversation_id: str | None,
    *,
    guest_active: bool,
    context: Context | None = None,
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
            # Calls through the original ID count as activity on that parent.
            # Do not bind or extend another authenticated user's native session.
            session = hass.data.get(DATA_CHAT_SESSION, {}).get(conversation_id)
            if context is not None and session is not None:
                user_id = getattr(session, "user_id", UNDEFINED)
                if user_id is UNDEFINED or user_id == context.user_id:
                    session.async_updated()
            return target
    return conversation_id


def bind_parent_conversation_id(
    hass: HomeAssistant | None,
    parent_id: str | None,
    target: str,
    owner: tuple[str, str],
) -> None:
    """Retain the actual owned log for retries through an unchanged parent ID."""
    if hass is not None and parent_id is not None and parent_id != target:
        history = _unclaimed_live_history(hass, parent_id)
        if (
            history is not None
            and hass.data.get(_CONVERSATION_ID_OWNERS, {}).get(target) == owner
        ):
            history.bindings[owner] = target


def claim_conversation_id(
    hass: HomeAssistant | None,
    agent_id: str | None,
    scope: ResolvedDataScope,
    conversation_id: str | None,
    *,
    guest_active: bool,
    guest_namespace: str | None = None,
    provisional_claim: ProvisionalConversationIDClaim | None = None,
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

    def claimed(target: str, *, created: bool) -> str:
        if provisional_claim is not None:
            provisional_claim.record(target, owner, created=created)
        return target

    namespace = (
        (guest_namespace or GUEST_CONTINUITY_NAMESPACE) if guest_active else None
    )
    existing = owners.get(conversation_id)
    history = _unclaimed_live_history(hass, conversation_id)
    if history is not None:
        target = history.bindings.get(owner)
        if (
            target is not None
            and owners.get(target) == owner
            and (
                guest_namespace is None
                or target.startswith(f"extended-openai-{guest_namespace}-{agent_id}-")
            )
        ):
            return claimed(target, created=False)
        target = create_conversation_id(agent_id, namespace)
        owners[target] = owner
        history.bindings[owner] = target
        return claimed(target, created=True)

    if existing is None:
        owners[conversation_id] = owner
        return claimed(conversation_id, created=True)

    if existing == owner:
        owners.pop(conversation_id, None)
        owners[conversation_id] = owner
        return claimed(conversation_id, created=False)

    target = create_conversation_id(agent_id, namespace)
    owners[target] = owner
    return claimed(target, created=True)


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
    ha_default: bool
    reset_history: bool = False


def register_conversation_id_cleanup(
    session: Any,
    hass: HomeAssistant | None,
    conversation_id: str,
    owner: tuple[str, str],
    *,
    ha_default: bool = False,
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
        previous.ha_default = ha_default
        return
    registration = _CleanupRegistration(session, owner, ha_default)
    registrations[conversation_id] = registration

    def release_claim() -> None:
        if (
            data.get(_CLEANUP_REGISTRATIONS) is registrations
            and registrations.get(conversation_id) is registration
        ):
            registrations.pop(conversation_id)
            release_conversation_id_claim(hass, conversation_id, owner)

    register(release_claim)


def mark_default_history_for_restore(
    hass: HomeAssistant, agent_id: str, retained_ids: frozenset[str]
) -> None:
    """Reset exact owned native logs at their next admitted turn after restore."""
    data = hass.data
    sessions = data.get(DATA_CHAT_SESSION, {})
    owners = data.get(_CONVERSATION_ID_OWNERS, {})
    for chat_id, registration in data.get(_CLEANUP_REGISTRATIONS, {}).items():
        if (
            registration.ha_default
            and registration.owner[0] == agent_id
            and owners.get(chat_id) == registration.owner
            and sessions.get(chat_id) is registration.session
            and chat_id not in retained_ids
        ):
            registration.reset_history = True


def take_default_history_reset(
    hass: HomeAssistant, session: ChatSession, admitted_log: ChatLog
) -> bool:
    """Consume a reset only for the admitted native session it belongs to."""
    registration = hass.data.get(_CLEANUP_REGISTRATIONS, {}).get(
        session.conversation_id
    )
    if (
        registration is None
        or registration.session is not session
        or not registration.reset_history
    ):
        return False
    cached_log = hass.data.get(DATA_CHAT_LOGS, {}).get(session.conversation_id)
    if cached_log is not None and cached_log is not admitted_log:
        # Older Core hands the turn a copy and can discard it after an abort.
        # Clear the owned backing log too so that an aborted turn cannot
        # resurrect its pre-restore context at the next admission.
        cached_log.content[:] = [SystemContent(content="")]
        cached_log.extra_system_prompt = None
        cached_log.llm_api = None
        if hasattr(cached_log, "turn_start"):
            cached_log.turn_start = len(cached_log.content)
    registration.reset_history = False
    return True
