"""Independent Guest script-template audit, through real HA and SDK transport."""

import json

import pytest

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import _agent, _say
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
)

CANARY = "audit_hidden_script_violet"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "guest_active,function_policy,hidden_read",
    [
        (True, "on", True),
        (False, "on", True),
        (True, "off", True),
        (True, "on", False),
    ],
)
async def test_guest_static_script_cannot_read_excluded_state(
    hass, monkeypatch, guest_active, function_policy, hidden_read
):
    public_entity = "input_boolean.audit_public_switch"
    private_entity = "input_text.audit_private_state"
    tool = {
        "spec": {
            "name": "public_toggle_and_status",
            "description": "Turn on the public switch and return status",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "variables": {
                "_function_result": "{{ states('input_text.audit_private_state') }}"
                if hidden_read
                else "public-ok"
            },
            "sequence": [
                {
                    "action": "input_boolean.turn_on",
                    "target": {"entity_id": public_entity},
                }
            ],
        },
    }
    agent = await _agent(
        hass,
        functions=[tool],
        guest_policy_version=2,
        guest_function_policy=function_policy,
        guest_excluded_entities=[private_entity],
    )
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"audit_public_switch": {"name": "Audit public switch"}}},
    )
    assert await async_setup_component(
        hass,
        "input_text",
        {
            "input_text": {
                "audit_private_state": {"name": "Audit private state", "max": 100}
            }
        },
    )
    await hass.services.async_call(
        "input_text",
        "set_value",
        {"entity_id": "input_text.audit_private_state", "value": CANARY},
        blocking=True,
    )
    await hass.async_block_till_done()
    async_expose_entity(hass, "conversation", public_entity, True)
    async_expose_entity(hass, "conversation", private_entity, True)
    if guest_active:
        await agent._guest_mode.async_update_trusted(indefinite=True)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(name="public_toggle_and_status", arguments={}),
            _chat_sse_text("Checked."),
        ],
    )
    result = await _say(hass, agent, "Turn on the public switch and give status")
    if guest_active and (function_policy == "off" or hidden_read):
        assert result.response.error_code is not None
        assert len(wire.requests) == 1
    else:
        assert result.response.error_code is None
        assert len(wire.requests) == 2
    names = [
        tool.get("function", {}).get("name")
        for tool in wire.requests[0]["body"].get("tools", [])
    ]
    if guest_active:
        assert CANARY not in json.dumps(wire.requests[0]["body"]), (
            "Hidden entity was already exposed in Guest prompt"
        )
    if guest_active and (function_policy == "off" or hidden_read):
        assert "public_toggle_and_status" not in names
        assert hass.states.get(public_entity).state == "off"
        assert CANARY not in json.dumps(wire.requests)
    elif not guest_active:
        assert "public_toggle_and_status" in names
        assert hass.states.get(public_entity).state == "on"
        assert CANARY in json.dumps(wire.requests[1]["body"]), (
            "Owner positive control did not execute state template"
        )
    else:
        assert "public_toggle_and_status" in names
        assert hass.states.get(public_entity).state == "on", (
            "Legitimate Guest public control was denied"
        )
        assert CANARY not in json.dumps(wire.requests[1]["body"]), (
            "Guest-allowed Script Function disclosed excluded state via configured variables"
        )
        if not hidden_read:
            assert "public-ok" in json.dumps(wire.requests[1]["body"])
