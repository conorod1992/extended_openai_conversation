"""Provider-wire acceptance for composed Function Tool execution."""

from __future__ import annotations

import json
from typing import Any

import voluptuous as vol

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    DOMAIN,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _responses_sse_tool_call,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _responses_sse_text,
    _speech,
)

_GROUP_ID = "composition-group"
_CUSTOM_TOOL_NAME = "composition_status"
_CUSTOM_RESULT = "composition-template-result"
_HA_ALIAS = "ha_composition_echo"
_HA_CALL_ID = "call-ha-composition"


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
    *,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _serialized(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


async def test_on_demand_custom_tool_load_execute_and_result_cross_real_wire(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A loaded Function Group must compose into a real executable provider loop."""
    tool = {
        "spec": {
            "name": _CUSTOM_TOOL_NAME,
            "description": "Return the deterministic composition status.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": _CUSTOM_RESULT},
        "enabled": True,
    }
    entry = _make_entry(
        "Function Composition",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [tool],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": _GROUP_ID,
                    "name": "Composition Group",
                    "description": "Load the composition status capability.",
                    "loading_mode": "on_demand",
                    "functions": [_CUSTOM_TOOL_NAME],
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                "call-load-composition",
                "load_function_groups",
                {"groups": [_GROUP_ID]},
            ),
            _chat_sse_tool_call(
                "call-composition-tool",
                _CUSTOM_TOOL_NAME,
                {},
            ),
            _chat_sse_text("Composition completed."),
        ],
    )

    result = await _say(hass, agent, "Load and run the composition status tool")

    assert _speech(result) == "Composition completed."
    assert [request["path"] for request in wire.requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]

    first = wire.requests[0]["body"]
    second = wire.requests[1]["body"]
    third = wire.requests[2]["body"]
    assert _CUSTOM_TOOL_NAME not in _tool_names(first, API_MODE_CHAT_COMPLETIONS)
    assert "load_function_groups" in _tool_names(first, API_MODE_CHAT_COMPLETIONS)
    assert _CUSTOM_TOOL_NAME in _tool_names(second, API_MODE_CHAT_COMPLETIONS)

    # The second SDK request is possible only after the production loader executes;
    # require its real result rather than merely checking the changed schema list.
    loader_message = next(
        item
        for item in second["messages"]
        if item.get("role") == "tool"
        and item.get("tool_call_id") == "call-load-composition"
    )
    loader_envelope = json.loads(loader_message["content"])
    loader_result = json.loads(loader_envelope["result"])
    assert loader_result["status"] == "success"
    assert loader_result["loaded"] == [_GROUP_ID]
    assert loader_result["unknown"] == []

    # The third request must contain the result produced by the configured template
    # implementation, proving resolution -> execution -> provider serialization.
    third_text = _serialized(third)
    assert "call-composition-tool" in third_text
    assert _CUSTOM_RESULT in third_text


class _MutableEchoTool(llm.Tool):
    """Synthetic HA-owned tool whose authoritative metadata can change live."""

    name = "composition_echo"
    description = "Echo through the initial Home Assistant composition schema."
    parameters = vol.Schema(
        {
            vol.Required("value"): str,
            vol.Optional("repeat", default=1): vol.All(int, vol.Range(min=1, max=3)),
        }
    )

    def __init__(self) -> None:
        self.calls: list[tuple[llm.ToolInput, llm.LLMContext]] = []

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        self.calls.append((tool_input, llm_context))
        repeat = tool_input.tool_args.get("repeat", 1)
        return {"echo": tool_input.tool_args["value"] * repeat}


class _MutableAPI(llm.API):
    """Small custom HA LLM API used through Home Assistant's real registry."""

    def __init__(self, hass: HomeAssistant, tool: _MutableEchoTool) -> None:
        super().__init__(
            hass=hass,
            id="extended_openai_function_composition_api",
            name="Function composition API",
        )
        self.tool = tool

    async def async_get_api_instance(
        self, context: llm.LLMContext
    ) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt="Use the composition echo tool when requested.",
            llm_context=context,
            tools=[self.tool],
        )


def _ha_reference(tool: llm.Tool, api: llm.API) -> dict[str, str]:
    return {
        "type": "ha_llm",
        "source_type": "api",
        "source_id": f"{type(tool).__module__}.{type(tool).__qualname__}",
        "api_id": api.id,
        "tool_name": tool.name,
    }


def _provider_tool(body: dict[str, Any], api_mode: str, name: str) -> dict[str, Any]:
    if api_mode == API_MODE_RESPONSES:
        return next(
            item
            for item in body.get("tools", [])
            if item.get("type") == "function" and item.get("name") == name
        )
    return next(
        item["function"]
        for item in body.get("tools", [])
        if item.get("type") == "function" and item["function"].get("name") == name
    )


async def test_ha_owned_tool_executes_and_live_schema_refreshes_after_reload(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """HA-owned execution and metadata must stay live through the provider seam."""
    # Restore class defaults in case this module is re-run in one Python process.
    _MutableEchoTool.description = (
        "Echo through the initial Home Assistant composition schema."
    )
    _MutableEchoTool.parameters = vol.Schema(
        {
            vol.Required("value"): str,
            vol.Optional("repeat", default=1): vol.All(int, vol.Range(min=1, max=3)),
        }
    )
    tool = _MutableEchoTool()
    api = _MutableAPI(hass, tool)
    llm.async_register_api(hass, api)
    reference = _ha_reference(tool, api)
    saved_tool = {
        "spec": {"name": _HA_ALIAS},
        "function": reference,
        "enabled": True,
    }

    entry = _make_entry(
        "HA Function Composition",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [saved_tool],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_call(
                _HA_CALL_ID,
                _HA_ALIAS,
                {"value": "ha", "repeat": 2},
            ),
            _responses_sse_text("HA composition completed."),
        ],
    )
    result = await _say(hass, agent, "Run the Home Assistant composition echo")

    assert _speech(result) == "HA composition completed."
    assert [request["path"] for request in wire.requests] == [
        "/v1/responses",
        "/v1/responses",
    ]
    initial_spec = _provider_tool(
        wire.requests[0]["body"], API_MODE_RESPONSES, _HA_ALIAS
    )
    assert initial_spec["description"] == _MutableEchoTool.description
    initial_properties = initial_spec["parameters"]["properties"]
    assert {"value", "repeat"} <= set(initial_properties)
    assert len(tool.calls) == 1
    assert tool.calls[0][0].tool_args == {"value": "ha", "repeat": 2}
    assert "haha" in _serialized(wire.requests[1]["body"])

    # HA owns this schema and description. Change them without touching the saved
    # ExtendedOpenAI reference, then cross a genuine config-entry reload boundary.
    updated_description = "Echo through the refreshed Home Assistant schema."
    _MutableEchoTool.description = updated_description
    _MutableEchoTool.parameters = vol.Schema(
        {
            vol.Required("message"): str,
            vol.Optional("uppercase", default=False): bool,
        }
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    refreshed_wire = _install_wire(
        monkeypatch,
        agent,
        [_responses_sse_text("Refreshed schema observed.")],
    )
    refreshed = await _say(hass, agent, "Inspect the refreshed HA tool")
    assert _speech(refreshed) == "Refreshed schema observed."

    refreshed_spec = _provider_tool(
        refreshed_wire.requests[0]["body"], API_MODE_RESPONSES, _HA_ALIAS
    )
    assert refreshed_spec["description"] == updated_description
    refreshed_properties = refreshed_spec["parameters"]["properties"]
    assert {"message", "uppercase"} <= set(refreshed_properties)
    assert "value" not in refreshed_properties
    assert "repeat" not in refreshed_properties
    # Only stable identity/name were persisted; live HA metadata changed underneath.
    assert saved_tool["spec"] == {"name": _HA_ALIAS}
