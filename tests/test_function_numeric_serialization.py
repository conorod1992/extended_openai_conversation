"""Management edits and runtime hydration agree on JSON/YAML numeric types."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
)
from custom_components.extended_openai_conversation_responses.function_configuration_repair import (
    function_tools_issue,
    isolated_function_tools,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_dependency_integrity import _call, _nested_rule
from tests.test_request_rules import MemoryStore


def numeric_tool():
    return {
        "spec": {
            "name": "fractional",
            "description": "Use fractional numeric configuration",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "variables": {"small": 0.000001, "large": 1e20, "literal": "1e-06"},
            "sequence": [{"stop": "Done"}],
        },
    }


@pytest.mark.parametrize("representation", ["structured", "json", "yaml"])
@pytest.mark.parametrize("schema", ["minimum", "enum", "variables"])
async def test_function_save_read_and_runtime_preserve_numbers(
    hass, management_agent, management_message, monkeypatch, representation, schema
):
    _entry, subentry = management_agent
    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    tool = numeric_tool()
    if schema != "variables":
        tool["spec"]["parameters"]["properties"] = {
            "value": {
                "type": "number",
                schema: [0.000001] if schema == "enum" else 0.000001,
            }
        }
    payload = (
        {"tool": tool}
        if representation == "structured"
        else {
            "yaml": json.dumps(tool)
            if representation == "json"
            else yaml.safe_dump(tool)
        }
    )
    result = await management_ui.async_management_command(
        hass, "admin", True, management_message("tools", "save", **payload)
    )
    assert result["functions"][-1] == tool
    configured = configured_function_tools_from_data(subentry.data)[-1]
    variables = configured["function"]["variables"].async_render(hass, {})
    assert variables == tool["function"]["variables"]
    assert type(variables["small"]) is float
    # The tolerant repair cache must give the same result as strict hydration.
    for value in ([tool], json.dumps([tool]), yaml.safe_dump([tool])):
        options = {"functions": value}
        assert function_tools_issue(options) == ([tool], None)
        assert isolated_function_tools(options) == ([tool], [], None)
        cached = configured_function_tools_from_data(options)[0]
        assert cached["function"]["variables"].async_render(hass, {}) == variables


async def test_yaml_mapping_keys_do_not_become_json_strings_in_caches(hass):
    tool = numeric_tool()
    tool["function"]["variables"]["lookup"] = {7: "numeric", "7": "literal"}
    options = {"functions": [tool]}
    assert function_tools_issue(options) == ([tool], None)
    cached = configured_function_tools_from_data(options)[0]
    assert cached["function"]["variables"].async_render(hass, {})["lookup"] == {
        7: "numeric",
        "7": "literal",
    }


async def test_numeric_enum_edit_cannot_break_existing_string_rule(
    hass, management_agent, management_message, monkeypatch
):
    _entry, subentry = management_agent
    tool = numeric_tool()
    tool["spec"]["parameters"]["properties"] = {"value": {"enum": ["1e-06"]}}
    subentry.data = {**subentry.data, "functions": yaml.safe_dump([tool])}
    rules = RequestRules(MemoryStore())
    await rules.async_initialize()
    rule = _nested_rule()
    rule["action"]["actions"] = [_call("fractional", {"value": "1e-06"})]
    rules._rules = [rule]
    rules._sort_and_compile()
    monkeypatch.setattr(
        management_ui, "async_get_request_rules", AsyncMock(return_value=rules)
    )
    before = deepcopy(subentry.data)
    tool["spec"]["parameters"]["properties"]["value"]["enum"] = [0.000001]
    with pytest.raises(HomeAssistantError):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            management_message("tools", "save", tool=tool, original_name="fractional"),
        )
    assert subentry.data == before
