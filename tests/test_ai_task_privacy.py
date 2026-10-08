"""Privacy regression tests for AI Task structured-output handling."""

import logging

import pytest

from custom_components.extended_openai_conversation_responses.ai_task import (
    parse_ai_task_structured_response,
)
from homeassistant.exceptions import HomeAssistantError


def test_malformed_structured_response_does_not_log_response_body(caplog) -> None:
    """Malformed model output may contain private task data and must not be logged."""
    private_body = '{private_user_content: "super-sensitive-value"}'

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HomeAssistantError, match="Error with structured response"):
            parse_ai_task_structured_response(private_body)

    assert "Failed to parse structured AI Task JSON response" in caplog.text
    assert private_body not in caplog.text
    assert "super-sensitive-value" not in caplog.text


def test_invalid_structure_does_not_expose_validation_values(caplog):
    import voluptuous as vol

    def private_validator(value):
        raise vol.Invalid(f"private value: {value}")

    schema = vol.Schema({vol.Required("answer"): private_validator})
    with pytest.raises(
        HomeAssistantError, match="does not match the requested structure"
    ) as error:
        parse_ai_task_structured_response('{"answer":"PRIVATE_TASK_MARKER"}', schema)
    assert "PRIVATE_TASK_MARKER" not in caplog.text
    assert "PRIVATE_TASK_MARKER" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("engine", ["voluptuous", "probatio"])
def test_supported_schema_engines_hide_invalid_values(engine, caplog):
    module = pytest.importorskip(engine)
    schema = module.Schema({module.Required("answer"): int})
    with pytest.raises(HomeAssistantError, match="does not match") as error:
        parse_ai_task_structured_response('{"answer":"PRIVATE_INVALID_VALUE"}', schema)
    formatted = "\n".join(logging.Formatter().format(record) for record in caplog.records)
    assert "PRIVATE_INVALID_VALUE" not in formatted
    assert error.value.__suppress_context__
