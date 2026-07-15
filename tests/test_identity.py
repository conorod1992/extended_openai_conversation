"""Tests for the fork's independent Home Assistant identity."""

from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_NAME,
    DEFAULT_WORKING_DIRECTORY,
    DOMAIN,
    EVENT_AUTOMATION_REGISTERED,
    EVENT_CONVERSATION_FINISHED,
)


def test_integration_identity_is_namespaced() -> None:
    """Verify the fork cannot collide with the original integration."""
    assert DOMAIN == "extended_openai_conversation_responses"
    assert DEFAULT_NAME == "Extended OpenAI Conversation (Responses)"
    assert DEFAULT_WORKING_DIRECTORY == "extended_openai_conversation_responses/"
    assert EVENT_AUTOMATION_REGISTERED.endswith(
        "extended_openai_conversation_responses"
    )
    assert EVENT_CONVERSATION_FINISHED.startswith(
        "extended_openai_conversation_responses."
    )
