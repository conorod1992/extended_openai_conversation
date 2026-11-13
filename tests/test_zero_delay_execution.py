"""Conversation delay normalization must preserve synchronous results."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DATA_DELAYED_TOOL_MANAGER,
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    split_legacy_execution_delay,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.template import Template


def _tool(hass, *, failure=False):
    return {
        "spec": {
            "name": "read_value",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                    "delay": {
                        "type": ["object", "null"],
                        "properties": {
                            unit: {"type": ["number", "null"], "minimum": 0}
                            for unit in ("hours", "minutes", "seconds")
                        },
                        "additionalProperties": False,
                    },
                },
            },
        },
        "function": {
            "type": "template",
            "value_template": Template(
                "{{ 1 / 0 }}" if failure else "{{ value + 1 }}", hass
            ),
            "parse_result": True,
        },
    }


def _agent(hass, tool, monkeypatch):
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        title="Agent",
        data={"functions": [tool], "function_groups": []},
    )
    entry = SimpleNamespace(
        entry_id="entry", disabled_by=None, data={}, subentries={"agent": subentry}
    )
    hass.config_entries.async_get_entry.return_value = entry
    agent = ExtendedOpenAIAgentEntity(entry, subentry)
    agent.hass = hass
    agent.entity_id = "conversation.agent"
    monkeypatch.setattr(agent, "_configured_function_tools_from_data", lambda _: [tool])
    monkeypatch.setattr(
        agent, "_resolve_live_guest_policy", GuestCapabilityPolicy.unrestricted
    )
    return agent


@pytest.mark.parametrize(
    "delay", [None, {}, {"seconds": 0}, {"hours": 0, "minutes": None, "seconds": 0.0}]
)
@pytest.mark.parametrize("failure", [False, True])
async def test_zero_delay_returns_actual_result_or_error(
    hass, monkeypatch, delay, failure
):
    tool = _tool(hass, failure=failure)
    entity = _agent(hass, tool, monkeypatch)
    # Use the real conversation executor, validator, splitter and template backend.
    result = await entity._execute_function_tool(
        tool,
        llm.ToolInput(
            id="call",
            tool_name="read_value",
            tool_args={"value": 6, "delay": delay},
            external=True,
        ),
        None,
        [],
    )
    data = tool_result_data(result)["result"]
    if failure:
        assert data["status"] == "error"
        assert (
            data["error"]
            == "Template rendering failed. Check the tool template and argument types."
        )
    else:
        assert data == "7"
    assert DATA_DELAYED_TOOL_MANAGER not in hass.data.get(DOMAIN, {})


async def test_positive_delay_remains_durably_scheduled(hass, monkeypatch):
    tool = _tool(hass)
    tool["function"]["value_template"] = "{{ value + 1 }}"
    manager = DelayedToolManager(hass)
    manager._setup_complete = True
    manager._store = SimpleNamespace(async_save=AsyncMock())
    hass.data.setdefault(DOMAIN, {})[DATA_DELAYED_TOOL_MANAGER] = manager
    entity = _agent(hass, tool, monkeypatch)
    result = await entity._execute_function_tool(
        tool,
        llm.ToolInput(
            id="call",
            tool_name="read_value",
            tool_args={"value": 6, "delay": {"seconds": 5}},
            external=True,
        ),
        None,
        [],
    )
    assert tool_result_data(result) == {"result": "Scheduled"}
    record = next(iter(manager._records.values()))
    assert manager._store.async_save.await_args.args[0] == {"calls": [record.as_dict()]}


@pytest.mark.parametrize(
    "delay",
    [
        {"minutes": 1, "seconds": -60},
        {"seconds": -1},
        {"seconds": "0"},
        {"seconds": False},
        {"seconds": float("nan")},
        {"other": 0},
    ],
)
def test_only_individually_numeric_zero_duration_fields_are_normalized(hass, delay):
    assert split_legacy_execution_delay(_tool(hass)["spec"], {"delay": delay}) == (
        {},
        delay,
    )


async def test_unrelated_delay_parameter_remains_backend_input(hass, monkeypatch):
    tool = _tool(hass)
    tool["spec"]["parameters"]["properties"]["delay"] = {"type": "integer"}
    tool["function"]["value_template"] = Template("{{ delay }}", hass)
    entity = _agent(hass, tool, monkeypatch)
    result = await entity._execute_function_tool(
        tool,
        llm.ToolInput(
            id="call", tool_name="read_value", tool_args={"delay": 0}, external=True
        ),
        None,
        [],
    )
    assert tool_result_data(result) == {"result": "0"}


@pytest.mark.parametrize(
    "delay",
    [
        {"seconds": -1},
        {"minutes": 1, "seconds": -60},
        {"seconds": "invalid"},
        {"seconds": False},
        {"unknown": 0},
    ],
)
async def test_invalid_delay_still_fails_conversation_validation(
    hass, monkeypatch, delay
):
    tool = _tool(hass)
    entity = _agent(hass, tool, monkeypatch)
    with pytest.raises(HomeAssistantError):
        await entity._execute_function_tool(
            tool,
            llm.ToolInput(
                id="call",
                tool_name="read_value",
                tool_args={"value": 6, "delay": delay},
                external=True,
            ),
            None,
            [],
        )
    assert DATA_DELAYED_TOOL_MANAGER not in hass.data.get(DOMAIN, {})
