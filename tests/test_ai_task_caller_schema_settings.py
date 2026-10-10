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
