"""On-demand Function Group loads and executes a real local tool on SDK wire."""

from __future__ import annotations

import json

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_tool_call,
    _chat_tool_result,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_stress.conftest import record


async def test_group_load_tool_execution_result_and_session_isolation(
    hass: HomeAssistant,
    monkeypatch,
    stress_trace: list[dict],
) -> None:
    tool_name = "enhanced_local_status"
    group_id = "enhanced-status-group"
    entry = _make_entry(
        "Enhanced function execution",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": tool_name,
                        "description": "Return the local status marker",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {
                        "type": "template",
                        "value_template": "LOCAL-TOOL-RESULT-東京",
                    },
                    "enabled": True,
                }
            ],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": group_id,
                    "name": "Enhanced status",
                    "description": "Nightly local result",
                    "loading_mode": "on_demand",
                    "functions": [tool_name],
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
                "call-load", "load_function_groups", {"groups": [group_id]}
            ),
            _chat_sse_tool_call("call-status", tool_name, {}),
            _chat_sse_text("Status delivered"),
            _chat_sse_text("Fresh session"),
        ],
    )

    async def say(text: str, conversation_id=None):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=entry.entry_id,
        )

    first = await say("Load and execute the local status tool")
    assert _speech(first) == "Status delivered"
    assert len(wire.requests) == 3
    names = [
        _tool_names(request["body"], API_MODE_CHAT_COMPLETIONS)
        for request in wire.requests
    ]
    assert "load_function_groups" in names[0] and tool_name not in names[0]
    assert tool_name in names[1] and tool_name in names[2]
    loaded = _chat_tool_result(wire.requests[1]["body"], "call-load")
    assert loaded["status"] == "success"
    tool_message = next(
        item
        for item in wire.requests[2]["body"]["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "call-status"
    )
    executed = json.loads(tool_message["content"])
    assert executed["result"] == "LOCAL-TOOL-RESULT-東京"

    other = await say("Start a separate conversation")
    assert _speech(other) == "Fresh session"
    assert other.conversation_id != first.conversation_id
    assert tool_name not in _tool_names(
        wire.requests[3]["body"], API_MODE_CHAT_COMPLETIONS
    )
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        public_turns=2,
        provider_requests=4,
        actual_tool_executions=2,
        local_function_executions=1,
        template_function_executions=1,
    )
