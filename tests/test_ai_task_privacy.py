"""Privacy regression tests for AI Task structured-output handling."""

import logging

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.ai_task import (
    _parse_structured_response,
)


def test_malformed_structured_response_does_not_log_response_body(caplog) -> None:
    """Malformed model output may contain private task data and must not be logged."""
    private_body = '{private_user_content: "super-sensitive-value"}'

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HomeAssistantError, match="Error with structured response"):
            _parse_structured_response(private_body)

    assert "Failed to parse structured AI Task JSON response" in caplog.text
    assert private_body not in caplog.text
    assert "super-sensitive-value" not in caplog.text
