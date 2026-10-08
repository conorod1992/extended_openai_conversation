"""Independent contracts for native scripts, failures and provider schemas."""

from copy import deepcopy

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.entity import (
    _format_structured_output,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    FunctionGroupRuntime,
    assemble_function_tools,
    load_function_groups,
)
from custom_components.extended_openai_conversation_responses.functions import (
    BashFunction,
    CompositeFunction,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    _native_result_sequence,
)
from custom_components.extended_openai_conversation_responses.safety_hardening import (
    _normalized_statistics_arguments,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import ObjectSelector, ObjectSelectorConfig
from tests.test_function_groups import _group, _tool


def test_captured_result_preserves_whole_data_template():
    actions = [
        {
            "action": "extended_openai_conversation_responses.execute_function",
            "data": {"name": "answer", "result_alias": "answer"},
        },
        {
            "action": "persistent_notification.create",
            "data": "{{ {'message': answer} }}",
        },
    ]
    original = deepcopy(actions)
    sequence = _native_result_sequence(actions, {})
    assert sequence[-1] == actions[-1]
    assert actions == original


@pytest.mark.parametrize(
    "guard", ["disabled", "timeout_type", "timeout_zero", "policy"]
)
async def test_bash_guards_stop_composite_before_later_step(hass, guard):
    config = {
        "type": "bash",
        "command": "echo safe",
        "allow_unsafe_shell": guard != "disabled",
    }
    arguments = {
        "timeout": "bad"
        if guard == "timeout_type"
        else 0
        if guard == "timeout_zero"
        else 1
    }
    if guard == "policy":
        config["command"] = "cd .."
    sequence = CompositeFunction().validate_schema(
        {
            "type": "composite",
            "sequence": [
                config,
                {"type": "template", "value_template": "later succeeded"},
            ],
        }
    )
    with pytest.raises(HomeAssistantError):
        await CompositeFunction().execute(hass, sequence, arguments, None, [])
    standalone = BashFunction().validate_schema(config)
    result = await BashFunction().execute(hass, standalone, arguments, None, [])
    assert "error" in result


def test_free_form_object_selector_is_rejected_before_strict_submission():
    caller = vol.Schema(
        {vol.Required("inventory"): ObjectSelector(ObjectSelectorConfig())}
    )
    assert caller({"inventory": {"arbitrary_key": 3}}) == {
        "inventory": {"arbitrary_key": 3}
    }
    with pytest.raises(HomeAssistantError, match="free-form"):
        _format_structured_output(caller, None)


@pytest.mark.parametrize("size,accepted", [(126, True), (127, False)])
def test_group_budget_counts_loader_and_reserved_builtin(size, accepted):
    tools = [_tool(f"tool_{index}") for index in range(size + 1)]
    groups = [
        _group("first", [tool["spec"]["name"] for tool in tools[:-1]]),
        _group("last", [tools[-1]["spec"]["name"]]),
    ]
    session = FunctionGroupRuntime().begin("boundary", 30)
    result = load_function_groups(
        session, ["first"], groups, tools, max_tools=128, extra_tool_count=1
    )
    assert (result["status"] == "success") == accepted
    assert session.loaded_group_ids == ({"first"} if accepted else set())
    assert (
        len(assemble_function_tools(tools, groups, set(session.loaded_group_ids)).tools)
        + 1
        <= 128
    )


@pytest.mark.parametrize("period", ["week", "year"])
def test_advertised_long_statistics_periods_are_accepted(period):
    _normalized_statistics_arguments(
        {
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-02-01T00:00:00+00:00",
            "period": period,
        }
    )
