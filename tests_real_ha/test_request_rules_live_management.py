"""Confirmed management requests exercise real HA and the SDK provider wire."""

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from tests_real_ha.test_provider_wire_e2e import _agent, _chat_sse_text, _install_wire


@pytest.mark.parametrize("matched", [False, True])
async def test_live_rule_request_reaches_provider(hass, monkeypatch, matched):
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    if matched:
        await agent._request_rules.async_create(
            {
                "name": "AI route",
                "phrases": ["hello"],
                "match_type": "equals",
                "action_type": "model_routing",
                "action": {
                    "model": "gpt-5.6",
                    "scope": "request",
                    "continue_to_ai": True,
                },
            }
        )
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Live provider response")])
    owner = await hass.auth.async_create_user(
        "Live request admin", group_ids=["system-admin"]
    )
    result = await async_management_command(
        hass,
        owner.id,
        True,
        {
            "section": "request_rules",
            "action": "test",
            "confirm": True,
            "entry_id": agent.entry.entry_id,
            "subentry_id": agent.subentry.subentry_id,
            "text": "hello",
        },
    )
    assert result["response"] == "Live provider response"
    assert result["conversation_id"]
    assert len(wire.requests) == 1
