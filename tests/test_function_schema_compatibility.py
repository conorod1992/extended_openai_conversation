"""Backward-compatibility coverage for configured Function Tool schemas."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.model_payload import (
    prepare_model_function_tools,
)


def _native_tool(parameters: dict) -> dict:
    return {
        "spec": {
            "name": "legacy_schema_tool",
            "description": "Tool created by an older integration release",
            "parameters": parameters,
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def test_enum_names_annotation_is_accepted_preserved_and_non_semantic() -> None:
    """Previously accepted enumNames metadata must not invalidate a saved tool."""
    parameters = {
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "enum": ["mobile", "home"],
                "enumNames": ["Mobile phone", "Home phone"],
            }
        },
        "required": ["phone"],
        "additionalProperties": False,
    }

    configured = validate_function_tools([_native_tool(parameters)])

    assert configured[0]["spec"]["parameters"] == parameters
    assert prepare_model_function_tools(configured)[0]["spec"]["parameters"] == parameters
    assert validate_function_arguments(configured[0]["spec"], {"phone": "mobile"}) == {
        "phone": "mobile"
    }

    with pytest.raises(HomeAssistantError, match="must be one of its choices"):
        validate_function_arguments(configured[0]["spec"], {"phone": "work"})


def test_enum_names_is_allowed_recursively() -> None:
    """Compatibility annotations remain valid at nested schema nodes."""
    parameters = {
        "type": "object",
        "properties": {
            "contacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "phone": {
                            "type": "string",
                            "enum": ["mobile", "home"],
                            "enumNames": ["Mobile phone", "Home phone"],
                        }
                    },
                    "required": ["phone"],
                    "additionalProperties": False,
                },
            }
        },
        "additionalProperties": False,
    }

    assert validate_function_tools([_native_tool(parameters)])[0]["spec"][
        "parameters"
    ] == parameters


def test_arbitrary_unknown_schema_annotation_remains_rejected() -> None:
    """Compatibility is explicit rather than allowing every unknown keyword."""
    parameters = {
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "x-display-names": ["Mobile phone", "Home phone"],
            }
        },
    }

    with pytest.raises(AgentConfigError, match="unsupported keyword"):
        validate_function_tools([_native_tool(parameters)])


def test_unsupported_semantic_schema_keyword_remains_rejected() -> None:
    """Semantic constraints stay fail-closed until local validation supports them."""
    parameters = {
        "type": "object",
        "properties": {
            "phone": {
                "oneOf": [
                    {"type": "string", "const": "mobile"},
                    {"type": "string", "const": "home"},
                ]
            }
        },
    }

    with pytest.raises(AgentConfigError, match="unsupported keyword"):
        validate_function_tools([_native_tool(parameters)])
