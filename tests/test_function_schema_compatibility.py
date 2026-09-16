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
    validate_function_schema,
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


def _configured(parameters: dict) -> dict:
    return validate_function_tools([_native_tool(parameters)])[0]


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

    configured = _configured(parameters)

    assert configured["spec"]["parameters"] == parameters
    assert prepare_model_function_tools([configured])[0]["spec"]["parameters"] == parameters
    assert validate_function_arguments(configured["spec"], {"phone": "mobile"}) == {
        "phone": "mobile"
    }

    with pytest.raises(HomeAssistantError, match="must be one of its choices"):
        validate_function_arguments(configured["spec"], {"phone": "work"})


def test_safe_annotations_are_accepted_recursively_and_preserved() -> None:
    """Descriptive JSON Schema metadata must not make an otherwise safe tool invalid."""
    parameters = {
        "type": "object",
        "title": "Date range",
        "description": "Query one inclusive date range",
        "examples": [{"start": "2026-09-01", "end": "2026-09-30"}],
        "deprecated": False,
        "readOnly": False,
        "writeOnly": False,
        "$comment": "Metadata only",
        "properties": {
            "start": {
                "type": "string",
                "title": "Start date",
                "format": "date",
                "default": "2026-09-01",
                "example": "2026-09-01",
                "markdownDescription": "Inclusive **start** date",
                "x-ui-order": 1,
            },
            "end": {
                "type": "string",
                "format": "date",
                "contentEncoding": "identity",
                "contentMediaType": "text/plain",
                "contentSchema": {"type": "string"},
                "enumDescriptions": ["End of the range"],
                "markdownEnumDescriptions": ["End of the **range**"],
                "x-home-assistant-hint": {"kind": "date"},
            },
        },
        "required": ["start", "end"],
        "additionalProperties": False,
    }

    configured = _configured(parameters)

    assert configured["spec"]["parameters"] == parameters
    assert prepare_model_function_tools([configured])[0]["spec"]["parameters"] == parameters
    assert validate_function_schema(parameters) == ()


def test_format_is_annotation_only_for_local_argument_validation() -> None:
    """format must be preserved without pretending the local validator asserts it."""
    parameters = {
        "type": "object",
        "properties": {
            "start": {"type": "string", "format": "date"},
            "end": {"type": "string", "format": "date"},
        },
        "required": ["start", "end"],
    }
    configured = _configured(parameters)

    assert validate_function_arguments(
        configured["spec"], {"start": "not-a-date", "end": "also-not-a-date"}
    ) == {"start": "not-a-date", "end": "also-not-a-date"}


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

    assert _configured(parameters)["spec"]["parameters"] == parameters


def test_x_prefixed_extension_metadata_is_annotation_only() -> None:
    """Explicitly namespaced extension metadata may coexist with enforced constraints."""
    parameters = {
        "type": "object",
        "x-editor": {"collapsed": False},
        "properties": {
            "phone": {
                "type": "string",
                "x-display-names": ["Mobile phone", "Home phone"],
                "minLength": 2,
            }
        },
    }
    configured = _configured(parameters)

    assert configured["spec"]["parameters"] == parameters
    assert validate_function_schema(parameters) == ()
    with pytest.raises(HomeAssistantError, match="at least 2 characters"):
        validate_function_arguments(configured["spec"], {"phone": "x"})


def test_unrecognized_unscoped_keyword_warns_but_remains_usable() -> None:
    """Unknown vocabulary should not brick an otherwise usable Function Tool."""
    parameters = {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "mysteryConstraint": True},
        },
    }

    warnings = validate_function_schema(parameters)
    configured = _configured(parameters)

    assert len(warnings) == 1
    assert "mysteryConstraint" in warnings[0]
    assert "does not recognize" in warnings[0]
    assert configured["spec"]["parameters"] == parameters
    assert prepare_model_function_tools([configured])[0]["spec"]["parameters"] == parameters
    assert validate_function_arguments(configured["spec"], {"phone": "work"}) == {
        "phone": "work"
    }


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("oneOf", [{"const": "mobile"}, {"const": "home"}]),
        ("anyOf", [{"type": "string"}, {"type": "null"}]),
        ("allOf", [{"type": "string"}]),
        ("not", {"const": "forbidden"}),
        ("if", {"properties": {"kind": {"const": "a"}}}),
        ("dependentRequired", {"credit_card": ["billing_address"]}),
        ("patternProperties", {"^x-": {"type": "string"}}),
        ("contains", {"type": "string"}),
        ("multipleOf", 5),
        ("$ref", "#/$defs/value"),
        ("$schema", "https://json-schema.org/draft/2020-12/schema"),
    ],
)
def test_unsupported_semantic_keywords_warn_and_are_preserved(
    keyword: str, value: object
) -> None:
    """Unsupported semantics remain provider-visible without blocking configuration."""
    child = {"type": "string", keyword: value}
    if keyword in {"dependentRequired", "patternProperties"}:
        child = {"type": "object", keyword: value}
    elif keyword == "contains":
        child = {"type": "array", keyword: value}
    elif keyword == "multipleOf":
        child = {"type": "integer", keyword: value}
    parameters = {
        "type": "object",
        "properties": {"value": child},
    }

    warnings = validate_function_schema(parameters)
    configured = _configured(parameters)

    assert len(warnings) == 1
    assert keyword in warnings[0]
    assert "does not validate" in warnings[0]
    assert configured["spec"]["parameters"] == parameters
    assert prepare_model_function_tools([configured])[0]["spec"]["parameters"] == parameters


def test_malformed_supported_schema_still_fails_closed() -> None:
    """Relaxed compatibility must not hide errors in semantics we do enforce."""
    parameters = {
        "type": "object",
        "properties": {"phone": {"type": "string", "minLength": -1}},
    }

    with pytest.raises(AgentConfigError, match="non-negative integer"):
        validate_function_tools([_native_tool(parameters)])


def test_supported_constraints_still_reject_invalid_arguments() -> None:
    """Warnings for unsupported vocabulary must not weaken supported assertions."""
    parameters = {
        "type": "object",
        "properties": {
            "amount": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "multipleOf": 2,
            }
        },
        "required": ["amount"],
    }
    configured = _configured(parameters)

    with pytest.raises(HomeAssistantError, match="at most 10"):
        validate_function_arguments(configured["spec"], {"amount": 12})
