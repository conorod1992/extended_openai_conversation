"""Saved Script Functions use HA's native per-run variable initialization."""

import asyncio
import json

import pytest

from homeassistant.components import conversation
from homeassistant.core import SupportsResponse
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent, _management
from tests_real_ha.test_provider_input_history import _transport
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _chat_sse_tool_call


def _tool(implementation):
    return {
        "spec": {
            "name": "configured_greeting",
            "description": "Return a greeting",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "greeting": {"type": "string"},
                },
            },
        },
        "function": implementation,
    }


async def _saved_agent(hass, implementation):
    agent = await _contract_agent(hass, memory_enabled=False)
    command = await _management(hass, agent)
    before = await command("get")
    saved = await command(
        "save",
        config={"functions": [_tool(implementation)]},
        revision=before["revision"],
    )
    assert saved["valid"], saved
    await hass.async_block_till_done()
    return conversation.async_get_agent(hass, agent.entry.entry_id)


@pytest.mark.parametrize(
    "kind", ["static", "template", "precedence", "sequence", "response", "composite"]
)
async def test_saved_script_definitions_reach_real_runner(hass, kind):
    implementation = {
        "type": "script",
        "variables": {"greeting": "Hello"},
        "sequence": [
            {"variables": {"_function_result": "{{ greeting | default('MISSING') }}"}}
        ],
    }
    arguments = {}
    expected = "Hello"
    if kind == "template":
        implementation["variables"]["greeting"] = "{{ 'Hello ' ~ value }}"
        arguments = {"value": "Alice"}
        expected = "Hello Alice"
    elif kind == "precedence":
        arguments = {"greeting": "Argument"}
        expected = "Argument"
    elif kind == "sequence":
        implementation["sequence"].insert(0, {"variables": {"greeting": "Sequence"}})
        expected = "Sequence"
    elif kind == "response":

        async def respond(call):
            return {"value": call.data["value"]}

        hass.services.async_register(
            "variable_probe",
            "respond",
            respond,
            supports_response=SupportsResponse.ONLY,
        )
        implementation["sequence"] = [
            {
                "action": "variable_probe.respond",
                "data": {"value": "{{ greeting }}"},
                "response_variable": "reply",
            },
            {"variables": {"_function_result": "{{ reply.value }}"}},
        ]
    elif kind == "composite":
        implementation = {"type": "composite", "sequence": [implementation]}
    agent = await _saved_agent(hass, implementation)
    # Invoke twice so hydrated definitions must render anew for each call.
    for value in ("Alice", "Bob") if kind == "template" else (None,):
        if value:
            arguments = {"value": value}
            expected = f"Hello {value}"
        async with _transport(
            agent.entry,
            [
                _chat_sse_tool_call(
                    f"call-{value or kind}", "configured_greeting", arguments
                ),
                _chat_sse_text("Done"),
            ],
        ) as wire:
            assert _speech(await _say(hass, agent, "Run the saved greeting")) == "Done"
            wire.assert_complete(2)
            result = next(
                item
                for item in wire.requests[1]["body"]["messages"]
                if item.get("role") == "tool"
            )
            assert json.loads(result["content"])["result"] == expected


async def test_concurrent_script_calls_keep_argument_derived_definitions_private(hass):
    implementation = {
        "type": "script",
        "variables": {"greeting": "{{ 'Hello ' ~ value }}"},
        "sequence": [
            {"delay": {"milliseconds": 10}},
            {"variables": {"_function_result": "{{ greeting }}"}},
        ],
    }
    agent = await _saved_agent(hass, implementation)
    async with _transport(
        agent.entry,
        [
            _chat_sse_tool_call(
                "call-alice", "configured_greeting", {"value": "Alice"}
            ),
            _chat_sse_tool_call("call-bob", "configured_greeting", {"value": "Bob"}),
            _chat_sse_text("Done"),
            _chat_sse_text("Done"),
        ],
    ) as wire:
        results = await asyncio.gather(
            _say(hass, agent, "Greet Alice"), _say(hass, agent, "Greet Bob")
        )
        assert [_speech(result) for result in results] == ["Done", "Done"]
        wire.assert_complete(4)
        outputs = {
            item["tool_call_id"]: json.loads(item["content"])["result"]
            for request in wire.requests
            for item in request["body"]["messages"]
            if item.get("role") == "tool"
        }
        assert outputs == {"call-alice": "Hello Alice", "call-bob": "Hello Bob"}
