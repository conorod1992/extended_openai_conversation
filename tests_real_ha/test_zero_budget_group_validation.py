"""Native WS preview/save and real SDK wire agree on zero-budget Groups."""

import json

import pytest
from tests_real_ha.test_entry_point_contract_matrix import _contract_agent
from tests_real_ha.test_cross_feature_acceptance import _say, _speech
from tests_real_ha.test_provider_wire_e2e import _install_wire, _responses_sse_text
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
    _management_response,
)


@pytest.mark.parametrize("enabled,budget", [(True, 0), (False, 0), (True, 1)])
async def test_zero_budget_group_ws_validation_matches_effective_wire(
    hass, hass_ws_client, monkeypatch, enabled, budget
):
    tool = {
        "spec": {
            "name": "probe",
            "description": "Probe",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "OK", "parse_result": False},
    }
    group = {
        "id": "probe-group",
        "name": "Probe",
        "description": "Probe tools",
        "enabled": enabled,
        "loading_mode": "on_demand",
        "functions": ["probe"],
    }
    agent = await _contract_agent(
        hass,
        chat_model="gpt-6-astra",
        reasoning_effort="medium",
        api_mode="responses",
        max_function_calls_per_conversation=budget,
        continue_conversation="ha_default",
        functions=[tool],
        function_groups=[group],
        skills=[],
    )
    client = await _admin_client(hass, hass_ws_client)
    before = await _management_call(
        client, entry=agent.entry, section="configuration", action="get"
    )
    wire = _install_wire(monkeypatch, agent, [_responses_sse_text("Initial response")])
    assert _speech(await _say(hass, agent, "A fresh request")) == "Initial response"
    assert len(wire.requests) == 1
    assert bool(wire.requests[0]["body"].get("tools")) is (budget > 0)
    changes = {"api_mode": "chat_completions"}
    preview = await _management_response(
        client,
        entry=agent.entry,
        section="configuration",
        action="request_preview",
        config=changes,
    )
    valid = await _management_call(
        client,
        entry=agent.entry,
        section="configuration",
        action="validate",
        config=changes,
    )
    saved = await _management_call(
        client,
        entry=agent.entry,
        section="configuration",
        action="save",
        config=changes,
        revision=before["revision"],
    )
    if budget:
        assert preview["success"] is False
        assert valid["valid"] is saved["valid"] is False
        assert "function/tool calling" in str(preview) + str(valid)
    else:
        assert preview["success"] is True, preview
        sections = {
            section["key"]: section["content"]
            for section in preview["result"]["sections"]
        }
        assert all(
            json.loads(sections[key]) == []
            for key in ("function_tools", "function_group_loader", "integration_tools")
        )
        assert valid["valid"] is saved["valid"] is True
        assert agent.subentry.data["api_mode"] == "chat_completions"
