"""Named restore participants for focused manager stand-ins."""

from typing import Any

from custom_components.extended_openai_conversation_responses.backup import (
    AgentDataParticipants,
)


def participants(**overrides: Any) -> AgentDataParticipants:
    """Fill unrelated participants without exposing a positional production API."""
    return AgentDataParticipants(
        **{
            "memory": object(),
            "temporary": object(),
            "knowledge": object(),
            "archive": object(),
            "usage": object(),
            "guest_mode": object(),
            "request_rules": object(),
            **overrides,
        }
    )


def participant_values(value: AgentDataParticipants) -> tuple[Any, ...]:
    """Observe every fake manager for cross-category state assertions."""
    return (
        value.memory,
        value.temporary,
        value.knowledge,
        value.archive,
        value.usage,
        value.guest_mode,
        value.request_rules,
    )
