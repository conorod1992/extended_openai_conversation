"""Caller validation after provider placeholders in composed AI Task schemas."""

from copy import deepcopy
import json

from jsonschema import Draft202012Validator
import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.ai_task import (
    _omit_optional_nulls,
    parse_ai_task_structured_response,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _adjust_schema,
)
from homeassistant.exceptions import HomeAssistantError


def _alternative_schema(keyword="anyOf"):
    return {
        keyword: [
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "note"},
                    "note": {"type": "string"},
                    "nullable": {"type": ["string", "null"]},
                },
                "required": ["kind", "nullable"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"const": "number"},
                    "number": {"type": "integer"},
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
        ]
    }


def _caller_structure(schema):
    validator = Draft202012Validator(schema)

    def validate(data):
        if not validator.is_valid(data):
            raise vol.Invalid("Caller validation failed")
        return data

    return vol.Schema(validate)


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
@pytest.mark.parametrize("array", [False, True])
def test_nested_alternative_placeholders_validate_for_caller(keyword, array):
    payload_schema = _alternative_schema(keyword)
    item = {"kind": "note", "note": None, "nullable": None}
    payload = [item, {"kind": "number", "number": None}] if array else item
    schema = {
        "type": "object",
        "properties": {
            "payload": {"type": "array", "items": payload_schema}
            if array
            else payload_schema
        },
        "required": ["payload"],
    }
    data = {"payload": payload}
    provider = deepcopy(schema)
    _adjust_schema(provider)
    assert Draft202012Validator(provider).is_valid(data)
    assert not Draft202012Validator(schema).is_valid(data)
    result = parse_ai_task_structured_response(
        json.dumps(data), _caller_structure(schema), original_schema=schema
    )
    expected = {"kind": "note", "nullable": None}
    assert result == {"payload": [expected, {"kind": "number"}] if array else expected}
    assert data["payload"] == payload
    assert schema["properties"]["payload"] == (
        {"type": "array", "items": payload_schema} if array else payload_schema
    )


def test_ambiguous_anyof_preserves_genuinely_valid_null():
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"note": {"type": "string"}}},
            {"type": "object", "properties": {"note": {"type": ["string", "null"]}}},
        ]
    }
    assert _omit_optional_nulls({"note": None}, schema) == {"note": None}


def test_oneof_chooses_valid_cleanup_with_fewest_removals():
    schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
            {
                "type": "object",
                "properties": {
                    "note": {"type": ["string", "null"]},
                    "detail": {"type": "string"},
                },
                "required": ["note"],
            },
        ]
    }
    assert _omit_optional_nulls({"note": None, "detail": None}, schema) == {
        "note": None
    }


def test_allof_preserves_required_nullable_field_and_cleans_optional_field():
    schema = {
        "allOf": [
            {
                "type": "object",
                "properties": {"note": {}, "detail": {"type": "string"}},
            },
            {
                "type": "object",
                "properties": {"note": {"type": ["string", "null"]}},
                "required": ["note"],
            },
        ]
    }
    assert _omit_optional_nulls({"note": None, "detail": None}, schema) == {
        "note": None
    }


def test_direct_object_and_openapi_nullable_behavior():
    schema = {
        "type": "object",
        "properties": {
            "optional": {"type": "string"},
            "nullable": {"type": "string", "nullable": True},
            "required": {"type": ["string", "null"]},
        },
        "required": ["required"],
    }
    assert _omit_optional_nulls(
        {"optional": None, "nullable": None, "required": None}, schema
    ) == {"nullable": None, "required": None}


def test_invalid_discriminator_and_required_null_are_not_repaired():
    schema = _alternative_schema()
    for data in (
        {"kind": "unknown", "note": None},
        {"kind": None, "note": None, "nullable": None},
    ):
        with pytest.raises(HomeAssistantError, match="does not match"):
            parse_ai_task_structured_response(
                json.dumps(data), _caller_structure(schema), original_schema=schema
            )


def test_large_array_builds_plan_once(monkeypatch):
    from custom_components.extended_openai_conversation_responses import ai_task

    original = ai_task._optional_null_plan
    calls = []

    def track(schema):
        calls.append(schema)
        return original(schema)

    monkeypatch.setattr(ai_task, "_optional_null_plan", track)
    schema = {"type": "array", "items": _alternative_schema()}
    data = [{"kind": "note", "note": None, "nullable": None}] * 1000
    result = _omit_optional_nulls(data, schema)
    assert result == [{"kind": "note", "nullable": None}] * 1000
    assert len(calls) < 20
