"""Optional tool fields retain their contract at the Responses boundary."""

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.request import (
    format_function_tools,
)
from homeassistant.exceptions import HomeAssistantError


def tool(schema):
    return {
        "spec": {"name": "custom", "parameters": schema},
        "function": {"type": "native", "name": "test"},
    }


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("explicit", [None, False, True])
@pytest.mark.parametrize(
    "location", ["root", "nested", "array", "composition", "definition"]
)
def test_optional_contract_is_only_set_on_responses(mode, explicit, location):
    schema = {"type": "object", "properties": {"note": {"type": "string"}}}
    if location == "nested":
        schema = {
            "type": "object",
            "properties": {"child": schema},
            "required": ["child"],
        }
    elif location == "array":
        schema = {
            "type": "object",
            "properties": {"children": {"type": "array", "items": schema}},
            "required": ["children"],
        }
    elif location == "composition":
        schema = {"allOf": [schema]}
    elif location == "definition":
        schema = {
            "type": "object",
            "properties": {"child": {"$ref": "#/$defs/child"}},
            "required": ["child"],
            "$defs": {"child": schema},
        }
    source = tool(schema)
    if explicit is not None:
        source["spec"]["strict"] = explicit
    before = deepcopy(source)
    payload = json.loads(json.dumps(format_function_tools([source], mode)))[0]
    spec = payload if mode == "responses" else payload["function"]
    expected = {**source["spec"]}
    if mode == "responses" and explicit is None:
        expected["strict"] = False
    assert {k: v for k, v in spec.items() if k != "type"} == expected
    assert source == before


def test_required_nullable_and_annotation_data_keep_policy():
    schema = {
        "type": "object",
        "properties": {"note": {"type": ["string", "null"]}},
        "required": ["note"],
        "default": {"properties": {"ignored": {"type": "string"}}},
    }
    source = tool(schema)
    assert "strict" not in format_function_tools([source], "responses")[0]
    assert validate_function_arguments(source["spec"], {"note": None}) == {"note": None}
    with pytest.raises(HomeAssistantError):
        validate_function_arguments(source["spec"], {})


def test_local_optional_null_is_still_rejected():
    source = tool(
        {
            "type": "object",
            "properties": {
                "note": {"type": "string"},
                "child": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                },
            },
        }
    )
    format_function_tools([source], "responses")
    assert validate_function_arguments(source["spec"], {}) == {}
    assert validate_function_arguments(source["spec"], {"child": {}}) == {"child": {}}
    for arguments in ({"note": None}, {"child": {"value": None}}):
        with pytest.raises(HomeAssistantError):
            validate_function_arguments(source["spec"], arguments)
