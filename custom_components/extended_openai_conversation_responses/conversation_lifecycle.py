"""Ephemeral lifecycle helpers for the current Assist conversation."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .continuity import ConversationContinuity
from .function_groups import get_function_group_runtime
from .request_rules import get_request_rule_runtime


@dataclass(frozen=True, slots=True)
class ConversationResetRequest:
    """Exact ephemeral session identifiers captured by a lifecycle tool call."""

    state_session_id: str
    memory_session_id: str


_ACTIVE_RESET: ContextVar[ConversationResetRequest | None] = ContextVar(
    "extended_openai_conversation_reset", default=None
)


def begin_conversation_lifecycle() -> Token[ConversationResetRequest | None]:
    """Start one request-local lifecycle context."""
    return _ACTIVE_RESET.set(None)


def end_conversation_lifecycle(
    token: Token[ConversationResetRequest | None],
) -> None:
    """Restore the previous lifecycle context."""
    _ACTIVE_RESET.reset(token)


def request_fresh_conversation(
    state_session_id: str | None, memory_session_id: str | None
) -> ConversationResetRequest:
    """Request a reset after the active response completes."""
    if state_session_id is None or memory_session_id is None:
        raise RuntimeError("No active conversation is available to reset")
    request = ConversationResetRequest(state_session_id, memory_session_id)
    _ACTIVE_RESET.set(request)
    return request


def requested_conversation_reset() -> ConversationResetRequest | None:
    """Return the reset requested by the current execution context."""
    return _ACTIVE_RESET.get()


async def async_reset_conversation_context(
    hass: HomeAssistant,
    continuity: ConversationContinuity,
    entry_id: str,
    subentry_id: str,
    *,
    continuity_key: str | None,
    state_session_id: str,
    memory_session_id: str,
) -> None:
    """Discard only ephemeral state associated with one logical conversation."""
    if continuity_key is not None:
        await continuity.async_request_end(continuity_key)
    elif state_session_id.startswith("conversation:"):
        conversation_id = state_session_id.removeprefix("conversation:")
        if conversation_id:
            await continuity.async_ignore_next_incoming_conversation_id(conversation_id)
    await continuity.async_clear_memory_bundle(memory_session_id)

    function_groups = get_function_group_runtime(hass, entry_id, subentry_id)
    if function_groups is not None:
        function_groups.end(state_session_id)

    get_request_rule_runtime(hass, entry_id, subentry_id).reset(state_session_id)
