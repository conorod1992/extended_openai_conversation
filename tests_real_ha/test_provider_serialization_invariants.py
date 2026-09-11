"""Raw-wire invariants for routing and provider-visible Function Tools."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _responses_sse_text,
    _speech,
)

_DEFAULT_MODEL = "gpt-5.6"
_DEFAULT_REASONING = "medium"
_ROUTED_MODEL = "gpt-6-astra"
_ROUTED_REASONING = "xhigh"


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


def _routing_rule() -> dict[str, Any]:
    """Return one request-scoped Continue-to-AI routing rule."""
    return {
        "name": "Raw-wire deep reasoning",
        "enabled": True,
        "phrases": ["think deeply"],
        "match_type": "starts_with",
        "action_type": "model_routing",
        "action": {
            "model": _ROUTED_MODEL,
            "reasoning_effort": _ROUTED_REASONING,
            "scope": "request",
            "reset": False,
            "continue_to_ai": True,
            "success_response": "Route selected",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


def _reasoning_effort(body: dict[str, Any], api_mode: str) -> str | None:
    """Read the API-specific raw serialized reasoning field."""
    if api_mode == API_MODE_RESPONSES:
        reasoning = body.get("reasoning")
        return reasoning.get("effort") if isinstance(reasoning, dict) else None
    return body.get("reasoning_effort")


@pytest.mark.parametrize(
    ("api_mode", "path"),
    [
        (API_MODE_CHAT_COMPLETIONS, "/v1/chat/completions"),
        (API_MODE_RESPONSES, "/v1/responses"),
    ],
)
async def test_request_scoped_routing_is_serialized_and_does_not_leak(
    hass: HomeAssistant,
    monkeypatch: Any,
    api_mode: str,
    path: str,
) -> None:
    """Routing overrides must reach raw HTTP once, then return to defaults."""
    entry = _make_entry(
        "Provider Serialization Routing",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: _DEFAULT_MODEL,
            CONF_REASONING_EFFORT: _DEFAULT_REASONING,
            CONF_FUNCTION_TOOLS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    await agent._request_rules.async_create(_routing_rule())

    replies = (
        [_chat_sse_text("Considered."), _chat_sse_text("Normal answer.")]
        if api_mode == API_MODE_CHAT_COMPLETIONS
        else [
            _responses_sse_text("Considered."),
            _responses_sse_text("Normal answer."),
        ]
    )
    wire = _install_wire(monkeypatch, agent, replies)

    first = await _say(hass, agent, "think deeply about this puzzle")
    assert _speech(first) == "Considered."
    second = await _say(
        hass,
        agent,
        "Now answer normally",
        conversation_id=first.conversation_id,
    )
    assert _speech(second) == "Normal answer."
    assert second.conversation_id == first.conversation_id

    assert [request["path"] for request in wire.requests] == [path, path]
    routed = wire.requests[0]["body"]
    normal = wire.requests[1]["body"]
    assert routed["model"] == _ROUTED_MODEL
    assert _reasoning_effort(routed, api_mode) == _ROUTED_REASONING
    assert normal["model"] == _DEFAULT_MODEL
    assert _reasoning_effort(normal, api_mode) == _DEFAULT_REASONING


def _template_tool(name: str, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "spec": {
            "name": name,
            "description": f"Deterministic serialization tool {name}.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": f"result:{name}"},
        "enabled": enabled,
    }


async def test_serialized_tool_exposure_respects_disabled_and_on_demand_groups(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Only the intended provider-visible tools may cross the raw HTTP seam."""
    always_name = "serialization_always"
    disabled_name = "serialization_disabled"
    intended_name = "serialization_intended"
    other_name = "serialization_other_group"
    intended_group = "serialization-intended-group"
    other_group = "serialization-other-group"

    entry = _make_entry(
        "Provider Serialization Tools",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: _DEFAULT_MODEL,
            CONF_FUNCTION_TOOLS: [
                _template_tool(always_name),
                _template_tool(disabled_name, enabled=False),
                _template_tool(intended_name),
                _template_tool(other_name),
            ],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": intended_group,
                    "name": "Intended serialization group",
                    "description": "Load only the intended serialization tool.",
                    "loading_mode": "on_demand",
                    "functions": [intended_name],
                    "enabled": True,
                },
                {
                    "id": other_group,
                    "name": "Other serialization group",
                    "description": "Must remain unloaded.",
                    "loading_mode": "on_demand",
                    "functions": [other_name],
                    "enabled": True,
                },
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
                "call-load-serialization-group",
                "load_function_groups",
                {"groups": [intended_group]},
            ),
            _chat_sse_text("Serialization exposure checked."),
        ],
    )
    result = await _say(hass, agent, "Load the intended serialization group")
    assert _speech(result) == "Serialization exposure checked."
    assert len(wire.requests) == 2

    initial_names = _tool_names(wire.requests[0]["body"], API_MODE_CHAT_COMPLETIONS)
    loaded_names = _tool_names(wire.requests[1]["body"], API_MODE_CHAT_COMPLETIONS)

    assert always_name in initial_names
    assert "load_function_groups" in initial_names
    assert disabled_name not in initial_names
    assert intended_name not in initial_names
    assert other_name not in initial_names

    assert always_name in loaded_names
    assert intended_name in loaded_names
    assert disabled_name not in loaded_names
    assert other_name not in loaded_names

    newly_exposed = loaded_names - initial_names
    assert newly_exposed == {intended_name}
