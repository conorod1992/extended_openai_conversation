"""Regressions for nested authoritative AI Task schema settings."""

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.ai_task import (
    parse_ai_task_structured_response,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("nested_schema", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_caller_cleanup_honors_nested_schema_required_policy(nested_schema, required):
    # A custom serializer may intentionally describe a more permissive field.
    # Native nested Schema and Any nodes own their required policy independently
    # of the containing Schema's required=True setting.
    child = (
        vol.Schema({"note": str}, required=required)
        if nested_schema
        else vol.Any({"note": str}, {"count": int}, required=required)
    )
    structure = vol.Schema({"payload": child}, required=True)
    projection = {
        "type": "object",
        "properties": {"payload": {"type": "object", "properties": {"note": {}}}},
        "required": ["payload"],
    }
    if required:
        with pytest.raises(HomeAssistantError, match="does not match"):
            parse_ai_task_structured_response(
                '{"payload":{"note":null}}', structure, original_schema=projection
            )
    else:
        assert parse_ai_task_structured_response(
            '{"payload":{"note":null}}', structure, original_schema=projection
        ) == {"payload": {}}


@pytest.mark.parametrize("nested_schema", [False, True])
def test_caller_cleanup_handles_multiple_list_item_alternatives(nested_schema):
    alternatives = [{"note": str}, {"count": int}]
    if nested_schema:
        alternatives = [vol.Schema(value) for value in alternatives]
    structure = vol.Schema({"payload": alternatives}, required=nested_schema)
    projection = {
        "type": "object",
        "properties": {"payload": {}},
        "required": ["payload"],
    }
    result = parse_ai_task_structured_response(
        '{"payload":[{"note":null}]}',
        structure,
        original_schema=projection,
    )
    assert result == {"payload": [{}]}
    with pytest.raises(HomeAssistantError, match="does not match"):
        parse_ai_task_structured_response(
            '{"payload":[{"note":7}]}', structure, original_schema=projection
        )


@pytest.mark.parametrize("wrappers", [1, 2])
def test_caller_cleanup_retains_wrapped_union_required_policy(wrappers):
    child = vol.Any({"note": str}, {"count": int}, required=False)
    for _ in range(wrappers):
        child = vol.Schema(child, required=True)
    structure = vol.Schema({"payload": child}, required=True)
    projection = {
        "type": "object",
        "properties": {"payload": {}},
        "required": ["payload"],
    }
    assert parse_ai_task_structured_response(
        '{"payload":{"note":null}}', structure, original_schema=projection
    ) == {"payload": {}}


def test_caller_cleanup_preserves_null_accepted_as_extra_by_another_item_branch():
    structure = vol.Schema(
        {"payload": [{"count": int}, {"note": str}]}, extra=vol.ALLOW_EXTRA
    )
    projection = {
        "type": "object",
        "properties": {"payload": {}},
        "required": ["payload"],
    }
    assert parse_ai_task_structured_response(
        '{"payload":[{"note":null}]}', structure, original_schema=projection
    ) == {"payload": [{"note": None}]}


@pytest.mark.parametrize("extra", [vol.ALLOW_EXTRA, vol.REMOVE_EXTRA])
def test_list_cleanup_matches_actual_engine_branch_semantics(extra):
    structure = vol.Schema({"payload": [{"note": str}, {"count": int}]}, extra=extra)
    projection = {
        "type": "object",
        "properties": {"payload": {}},
        "required": ["payload"],
    }
    # Legacy Voluptuous stops at a deeper error in the first branch. Current HA
    # can accept the later branch. Cleanup must respect the active caller engine.
    try:
        expected = structure({"payload": [{"note": None}]})
    except vol.Invalid:
        expected = {"payload": [{}]}
    assert (
        parse_ai_task_structured_response(
            '{"payload":[{"note":null}]}', structure, original_schema=projection
        )
        == expected
    )
