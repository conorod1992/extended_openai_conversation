"""Provider-wire acceptance for composed Function Tool execution."""

from __future__ import annotations

import json
from typing import Any

import pytest
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
from homeassistant.exceptions import HomeAssistantError
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
    _response_object,
    _responses_sse_text,
    _speech,
)

_GROUP_ID = "composition-group"
_CUSTOM_TOOL_NAME = "composition_status"
_CUSTOM_RESULT = "composition-template-result"
_HA_ALIAS = "ha_composition_echo"
_HA_CALL_ID = "call-ha-composition"
_HA_FAILURE_ALIAS = "ha_composition_entity_action"


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


def _responses_sse_tool_calls(
    calls: list[tuple[str, str, dict[str, Any]]],
) -> bytes:
    """Return one real Responses API stream containing ordered function calls."""
    items: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    sequence_number = 0
    for output_index, (call_id, name, arguments) in enumerate(calls):
        item = {
            "id": f"fc-{call_id}",
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments, separators=(",", ":")),
            "status": "completed",
        }
        items.append(item)
        events.append(
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {**item, "arguments": "", "status": "in_progress"},
                "sequence_number": sequence_number,
            }
        )
        sequence_number += 1
        events.append(
            {
                "type": "response.output_item.done",
                "output_index": output_index,
                "item": item,
                "sequence_number": sequence_number,
            }
        )
        sequence_number += 1
    events.append(
        {
            "type": "response.completed",
            "response": _response_object("resp-ha-composition-batch", items),
            "sequence_number": sequence_number,
        }
    )
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


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


class _EntityActionTool(llm.Tool):
    """Synthetic HA-owned entity action with observable dispatch boundaries."""

    name = "composition_entity_action"
    description = "Read one Home Assistant entity at execution time."
    parameters = vol.Schema({vol.Required("entity_id"): str})

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self.calls: list[str] = []

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        del llm_context
        entity_id = tool_input.tool_args["entity_id"]
        self.attempts.append(entity_id)
        state = hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError(f"Entity {entity_id} is unavailable")
        self.calls.append(entity_id)
        return {"entity_id": entity_id, "state": state.state}


class _MutableAPI(llm.API):
    """Small custom HA LLM API used through Home Assistant's real registry."""

    def __init__(self, hass: HomeAssistant, tool: llm.Tool) -> None:
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
            api_prompt="Use the composition Home Assistant tool when requested.",
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
    assert saved_tool["spec"] == {"name": _HA_ALIAS}


async def test_ha_owned_tool_runtime_failure_skips_later_call_and_next_turn_recovers(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A runtime HA-tool failure aborts later siblings without poisoning recovery."""
    entity_a = "sensor.composition_valid_a"
    entity_b = "sensor.composition_stale_b"
    entity_c = "sensor.composition_valid_c"
    for entity_id in (entity_a, entity_b, entity_c):
        hass.states.async_set(entity_id, "ready")
    await hass.async_block_till_done()

    tool = _EntityActionTool()
    api = _MutableAPI(hass, tool)
    llm.async_register_api(hass, api)
    saved_tool = {
        "spec": {"name": _HA_FAILURE_ALIAS},
        "function": _ha_reference(tool, api),
        "enabled": True,
    }
    entry = _make_entry(
        "HA Function Failure Isolation",
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

    captured_results: list[conversation.ToolResultContent] = []
    original_add = conversation.ChatLog.async_add_assistant_content_without_tools

    def capture_tool_results(
        chat_log: conversation.ChatLog,
        content: conversation.Content,
    ) -> None:
        if isinstance(content, conversation.ToolResultContent):
            captured_results.append(content)
        original_add(chat_log, content)

    monkeypatch.setattr(
        conversation.ChatLog,
        "async_add_assistant_content_without_tools",
        capture_tool_results,
    )

    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_calls(
                [
                    (
                        "call-ha-valid-a",
                        _HA_FAILURE_ALIAS,
                        {"entity_id": entity_a},
                    ),
                    (
                        "call-ha-stale-b",
                        _HA_FAILURE_ALIAS,
                        {"entity_id": entity_b},
                    ),
                    (
                        "call-ha-valid-c",
                        _HA_FAILURE_ALIAS,
                        {"entity_id": entity_c},
                    ),
                ]
            )
        ],
    )

    hass.states.async_remove(entity_b)
    await hass.async_block_till_done()

    with pytest.raises(
        HomeAssistantError,
        match=r"sensor\.composition_stale_b.*unavailable",
    ):
        await _say(hass, agent, "Run the ordered Home Assistant entity actions")

    assert tool.attempts == [entity_a, entity_b]
    assert tool.calls == [entity_a]
    assert hass.states.get(entity_a) is not None
    assert hass.states.get(entity_b) is None
    assert hass.states.get(entity_c) is not None
    assert [request["path"] for request in failing_wire.requests] == ["/v1/responses"]

    failed_batch_ids = {
        "call-ha-valid-a",
        "call-ha-stale-b",
        "call-ha-valid-c",
    }
    failed_batch_results = {
        result.tool_call_id: result
        for result in captured_results
        if result.tool_call_id in failed_batch_ids
    }
    assert set(failed_batch_results) == failed_batch_ids
    success_a = failed_batch_results["call-ha-valid-a"].tool_result
    assert entity_a in _serialized(success_a)
    assert "ready" in _serialized(success_a)

    error_b = failed_batch_results["call-ha-stale-b"].tool_result["result"]
    assert error_b["status"] == "error"
    assert "HomeAssistantError" in error_b["error"]
    assert entity_b in error_b["error"]
    assert "unavailable" in error_b["error"]

    skipped_c = failed_batch_results["call-ha-valid-c"].tool_result["result"]
    assert skipped_c["status"] == "skipped"
    assert "failed" in skipped_c["error"].lower()

    recovery_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_call(
                "call-ha-recovery",
                _HA_FAILURE_ALIAS,
                {"entity_id": entity_c},
            ),
            _responses_sse_text("HA recovery completed."),
        ],
    )
    result = await _say(hass, agent, "Run one clean Home Assistant entity action")

    assert _speech(result) == "HA recovery completed."
    assert tool.attempts == [entity_a, entity_b, entity_c]
    assert tool.calls == [entity_a, entity_c]
    assert [request["path"] for request in recovery_wire.requests] == [
        "/v1/responses",
        "/v1/responses",
    ]
    recovery_request = _serialized(recovery_wire.requests[1]["body"])
    assert "call-ha-recovery" in recovery_request
    assert entity_c in recovery_request
    assert "ready" in recovery_request
    assert "call-ha-stale-b" not in recovery_request
    assert "call-ha-valid-c" not in recovery_request
    assert '"status": "skipped"' not in recovery_request
    assert '"status": "error"' not in recovery_request
