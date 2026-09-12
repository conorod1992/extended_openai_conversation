"""Focused Real-HA acceptance for high-risk feature interactions."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_POLICY_VERSION,
    CONF_REASONING_EFFORT,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_USER,
    GUEST_POLICY_VERSION,
    TEMPORARY_MEMORY_BALANCED,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockUser

from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _action, _provider, _rule, _speech
from tests_real_ha.test_function_execution_composition import (
    _MutableAPI,
    _MutableEchoTool,
    _ha_reference,
)

_OWNER_ID = "cross-feature-owner"
_OWNER_SCOPE = f"user:{_OWNER_ID}"
_TEMPORARY_FACT = "The private temporary launch code is glacier-seven."
_HA_ALIAS = "ha_cross_feature_echo"
_GROUP_ID = "reload-status-group"
_GROUP_TOOL = "reload_status"


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
    conversation_id: str | None = None,
    *,
    user_id: str | None = None,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _system_prompt(request: dict[str, Any]) -> str:
    """Return the serialized Chat Completions system prompt."""
    return str(next(item for item in request["messages"] if item["role"] == "system")["content"])


def _tool_names(request: dict[str, Any]) -> set[str]:
    """Return Chat Completions function names advertised on one request."""
    return {
        item["function"]["name"]
        for item in request.get("tools", [])
        if item.get("type") == "function"
    }


async def test_temporary_memory_is_hidden_during_guest_mode_and_restored_afterwards(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Guest Mode must hide, not destroy, owner-scoped Temporary Memory."""
    MockUser(id=_OWNER_ID, name="Cross-feature Owner").add_to_hass(hass)
    entry = _make_entry(
        "Temporary Memory Guest Boundary",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: "chat_completions",
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    temporary = agent._temporary_memory
    assert temporary is not None

    record = await temporary.async_add(
        _OWNER_SCOPE,
        _TEMPORARY_FACT,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        category="acceptance",
        owner_scope_id=_OWNER_SCOPE,
    )

    owner_sent = _provider(monkeypatch, agent, ["I can see your temporary launch code."])
    owner = await _say(
        hass,
        agent,
        "What temporary launch code are you holding?",
        user_id=_OWNER_ID,
    )
    assert _speech(owner) == "I can see your temporary launch code."
    assert _TEMPORARY_FACT in _system_prompt(owner_sent[0])

    await agent._guest_mode.async_update_trusted(indefinite=True)
    guest_sent = _provider(monkeypatch, agent, ["That private context is unavailable in Guest Mode."])
    guest = await _say(
        hass,
        agent,
        "What temporary launch code are you holding?",
        owner.conversation_id,
        user_id=_OWNER_ID,
    )
    assert _speech(guest) == "That private context is unavailable in Guest Mode."
    assert _TEMPORARY_FACT not in _system_prompt(guest_sent[0])
    assert "temporary_memory_add" not in _tool_names(guest_sent[0])

    still_owned = await temporary.async_active(_OWNER_SCOPE, owner_scope_id=_OWNER_SCOPE)
    assert [item.memory_id for item in still_owned] == [record.memory_id]

    await agent._guest_mode.async_disable_trusted()
    restored_sent = _provider(monkeypatch, agent, ["Your launch code is available again."])
    restored = await _say(
        hass,
        agent,
        "Can you use my temporary launch code again?",
        owner.conversation_id,
        user_id=_OWNER_ID,
    )
    assert _speech(restored) == "Your launch code is available again."
    assert _TEMPORARY_FACT in _system_prompt(restored_sent[0])


async def test_request_scoped_model_route_survives_ha_owned_tool_loop_without_leaking(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A request route must stay active across an HA tool loop, then reset."""
    tool = _MutableEchoTool()
    api = _MutableAPI(hass, tool)
    llm.async_register_api(hass, api)
    saved_tool = {
        "spec": {"name": _HA_ALIAS},
        "function": _ha_reference(tool, api),
        "enabled": True,
    }
    entry = _make_entry(
        "Routed HA Tool Loop",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: "chat_completions",
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "medium",
            CONF_FUNCTION_TOOLS: [saved_tool],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    await agent._request_rules.async_create(
        _rule(
            "model_routing",
            {
                "model": "gpt-6-astra",
                "reasoning_effort": "xhigh",
                "scope": "request",
                "reset": False,
                "success_response": "Route selected",
            },
            match_type="starts_with",
            phrase="route and echo",
        )
    )

    sent = _provider(
        monkeypatch,
        agent,
        [
            {
                "index": 0,
                "id": "call-routed-ha-echo",
                "type": "function",
                "function": {
                    "name": _HA_ALIAS,
                    "arguments": json.dumps({"value": "ha", "repeat": 2}),
                },
            },
            "Routed tool complete.",
            "Back on the default route.",
        ],
    )

    routed = await _say(hass, agent, "route and echo this through Home Assistant")
    assert _speech(routed) == "Routed tool complete."
    assert len(sent) == 2
    assert [request["model"] for request in sent] == ["gpt-6-astra", "gpt-6-astra"]
    assert [request["reasoning_effort"] for request in sent] == ["xhigh", "xhigh"]
    assert _HA_ALIAS in _tool_names(sent[0])
    assert len(tool.calls) == 1
    assert tool.calls[0][0].tool_args == {"value": "ha", "repeat": 2}
    assert "haha" in json.dumps(sent[1], sort_keys=True)

    normal = await _say(hass, agent, "Use the normal route now", routed.conversation_id)
    assert _speech(normal) == "Back on the default route."
    assert len(sent) == 3
    assert sent[2]["model"] == "gpt-5.6"
    assert sent[2]["reasoning_effort"] == "medium"


async def test_persisted_request_rule_and_function_group_reconstruct_after_reload(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Durable feature config survives reload while runtime group state starts fresh."""
    tool = {
        "spec": {
            "name": _GROUP_TOOL,
            "description": "Return the deterministic post-reload status.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "reloaded-ready"},
        "enabled": True,
    }
    entry = _make_entry(
        "Reloaded Cross-feature State",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: "chat_completions",
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_FUNCTION_TOOLS: [tool],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": _GROUP_ID,
                    "name": "Reload status",
                    "description": "Load the post-reload status function.",
                    "loading_mode": "on_demand",
                    "functions": [_GROUP_TOOL],
                    "enabled": True,
                }
            ],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    calls: list[Any] = []

    async def turn_off(call: Any) -> None:
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.reload_bedroom", "on")
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [_action("light.reload_bedroom")],
                "success_response": "Reload rule executed.",
                "failure_response": "Reload rule failed.",
            },
        )
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = conversation.async_get_agent(hass, entry.entry_id)
    assert reloaded is not None

    sent = _provider(
        monkeypatch,
        reloaded,
        [
            {
                "index": 0,
                "id": "call-load-reload-group",
                "type": "function",
                "function": {
                    "name": "load_function_groups",
                    "arguments": json.dumps({"groups": [_GROUP_ID]}),
                },
            },
            "Reloaded group loaded.",
        ],
    )

    local = await _say(hass, reloaded, "good night")
    assert _speech(local) == "Reload rule executed."
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["light.reload_bedroom"]
    assert sent == []

    grouped = await _say(hass, reloaded, "Load my post-reload status capability")
    assert _speech(grouped) == "Reloaded group loaded."
    assert len(sent) == 2
    assert _GROUP_TOOL not in _tool_names(sent[0])
    assert "load_function_groups" in _tool_names(sent[0])
    assert _GROUP_TOOL in _tool_names(sent[1])
    loader = next(
        item
        for item in sent[1]["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "call-load-reload-group"
    )
    result = json.loads(json.loads(loader["content"])["result"])
    assert result["status"] == "success"
    assert result["loaded"] == [_GROUP_ID]
