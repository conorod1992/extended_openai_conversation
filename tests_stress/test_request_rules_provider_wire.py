"""Generated Request Rule cases across public Assist and SDK transport."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _speech,
)
from tests_stress.conftest import record
from tests_stress.test_request_rules_matrix import CLASSIFIED_MATCHERS

MATCHERS = sorted(CLASSIFIED_MATCHERS)
API_MODES = [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES]


async def _agent(hass: HomeAssistant, api_mode: str):
    entry = _make_entry(
        "Enhanced Rule Wire",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "medium",
            CONF_FUNCTION_TOOLS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    return agent


async def _say(hass: HomeAssistant, agent: Any, text: str, conversation_id=None):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _rule(match_type: str, action_type: str, action: dict) -> dict:
    return {
        "name": f"Enhanced {match_type} {action_type}",
        "enabled": True,
        "phrases": ["think deeply"],
        "match_type": match_type,
        "action_type": action_type,
        "action": action,
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


@pytest.mark.parametrize("match_type", MATCHERS)
@pytest.mark.parametrize("scope", ["request", "conversation"])
@pytest.mark.parametrize("api_mode", API_MODES)
async def test_matcher_route_scope_reaches_wire_and_next_turn(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    match_type: str,
    scope: str,
    api_mode: str,
    stress_trace: list[dict],
) -> None:
    agent = await _agent(hass, api_mode)
    created = await agent._request_rules.async_create(
        _rule(
            match_type,
            "model_routing",
            {
                "model": "gpt-6-astra",
                "reasoning_effort": "xhigh",
                "scope": scope,
                "reset": False,
                "continue_to_ai": True,
                "success_response": "Route selected",
            },
        )
    )
    preview = request_rule_match_preview(agent._request_rules.match("think deeply"))
    assert preview["matched"]
    assert preview["rule"]["id"] == created["id"]
    reply = (
        _chat_sse_text if api_mode == API_MODE_CHAT_COMPLETIONS else _responses_sse_text
    )
    wire = _install_wire(monkeypatch, agent, [reply("routed"), reply("followup")])
    first = await _say(hass, agent, "think deeply")
    assert _speech(first) == "routed"
    second = await _say(hass, agent, "ordinary request", first.conversation_id)
    assert _speech(second) == "followup"
    assert len(wire.requests) == 2
    assert wire.requests[0]["body"]["model"] == "gpt-6-astra"
    assert wire.requests[1]["body"]["model"] == (
        "gpt-6-astra" if scope == "conversation" else "gpt-5.6"
    )
    record(
        stress_trace,
        "summary",
        layer="provider-wire",
        matcher=match_type,
        scope=scope,
        api_mode=api_mode,
        public_turns=2,
        provider_requests=2,
    )


@pytest.mark.parametrize("match_type", MATCHERS)
async def test_matcher_local_action_calls_ha_without_provider(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    match_type: str,
    stress_trace: list[dict],
) -> None:
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    calls = []

    async def turn_off(call):
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.enhanced_rule", "on")
    await agent._request_rules.async_create(
        _rule(
            match_type,
            "local_action",
            {
                "actions": [
                    {
                        "domain": "light",
                        "service": "turn_off",
                        "target": {"entity_id": ["light.enhanced_rule"]},
                        "data": {},
                    }
                ],
                "success_response": "Local action complete",
            },
        )
    )
    wire = _install_wire(monkeypatch, agent, [])
    result = await _say(hass, agent, "think deeply")
    assert _speech(result) == "Local action complete"
    assert len(calls) == 1
    assert not wire.requests
    record(
        stress_trace,
        "summary",
        layer="real-ha",
        matcher=match_type,
        public_turns=1,
        ha_service_calls=1,
        provider_requests=0,
    )
