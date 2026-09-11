"""Acceptance for deliberate live configuration mutation semantics."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    assemble_function_tools,
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
    _speech,
)

_GROUP_ID = "live-group"
_TOOL_NAME = "live_status"
_REPLACEMENT_TOOL_NAME = "live_status_v2"


def _tool(name: str, description: str, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "spec": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": description},
        "enabled": enabled,
    }


def _group(functions: list[str], *, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": _GROUP_ID,
        "name": "Live Group",
        "description": "Functions whose definitions are resolved live.",
        "loading_mode": "on_demand",
        "functions": functions,
        "enabled": enabled,
    }


def _names(assembly) -> set[str]:
    return {tool["spec"]["name"] for tool in assembly.tools}


def _rule(*, model: str = "gpt-6-astra", enabled: bool = True) -> dict[str, Any]:
    return {
        "name": "Persistent conversation route",
        "enabled": enabled,
        "phrases": ["use the deep route"],
        "match_type": "equals",
        "action_type": "model_routing",
        "action": {
            "model": model,
            "reasoning_effort": "xhigh",
            "scope": "conversation",
            "reset": False,
            "continue_to_ai": True,
            "success_response": "Route selected",
        },
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def test_loaded_group_selection_resolves_current_configuration_each_turn() -> None:
    """Loaded group IDs select live definitions rather than frozen tool snapshots."""
    loaded = {_GROUP_ID}
    initial_tool = _tool(_TOOL_NAME, "Initial live definition")
    initial = assemble_function_tools([initial_tool], [_group([_TOOL_NAME])], loaded)
    assert _names(initial) == {_TOOL_NAME}
    assert loaded == {_GROUP_ID}
    assert initial.tools[0]["spec"]["description"] == "Initial live definition"

    edited_tool = _tool(_TOOL_NAME, "Edited live definition")
    edited = assemble_function_tools([edited_tool], [_group([_TOOL_NAME])], loaded)
    assert _names(edited) == {_TOOL_NAME}
    assert loaded == {_GROUP_ID}
    assert edited.tools[0]["spec"]["description"] == "Edited live definition"

    replacement = _tool(_REPLACEMENT_TOOL_NAME, "Replacement group member")
    remapped = assemble_function_tools(
        [replacement], [_group([_REPLACEMENT_TOOL_NAME])], loaded
    )
    assert _names(remapped) == {_REPLACEMENT_TOOL_NAME}
    assert loaded == {_GROUP_ID}

    disabled_tool = _tool(_REPLACEMENT_TOOL_NAME, "Disabled member", enabled=False)
    unavailable = assemble_function_tools(
        [disabled_tool], [_group([_REPLACEMENT_TOOL_NAME])], loaded
    )
    assert _REPLACEMENT_TOOL_NAME not in _names(unavailable)
    assert loaded == set()

    loaded.add(_GROUP_ID)
    disabled_group = assemble_function_tools(
        [replacement], [_group([_REPLACEMENT_TOOL_NAME], enabled=False)], loaded
    )
    assert _REPLACEMENT_TOOL_NAME not in _names(disabled_group)
    assert loaded == set()


async def test_conversation_route_survives_rule_edit_disable_and_delete(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A matched conversation route is copied state, not a live rule reference."""
    entry = _make_entry(
        "Live routing semantics",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "medium",
            CONF_FUNCTION_TOOLS: [],
            CONF_FUNCTION_GROUPS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    created = await agent._request_rules.async_create(_rule())
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text("Routed first turn."),
            _chat_sse_text("Edited rule did not rewrite state."),
            _chat_sse_text("Disabled rule did not rewrite state."),
            _chat_sse_text("Deleted rule did not rewrite state."),
        ],
    )

    first = await _say(hass, agent, "use the deep route")
    assert _speech(first) == "Routed first turn."
    assert wire.requests[0]["body"]["model"] == "gpt-6-astra"
    assert wire.requests[0]["body"]["reasoning_effort"] == "xhigh"

    edited_rule = deepcopy(created)
    edited_rule["action"] = {
        **edited_rule["action"],
        "model": "gpt-5.6",
        "reasoning_effort": "medium",
    }
    await agent._request_rules.async_update(created["id"], edited_rule)
    second = await _say(hass, agent, "continue after editing", first.conversation_id)
    assert _speech(second) == "Edited rule did not rewrite state."
    assert wire.requests[1]["body"]["model"] == "gpt-6-astra"
    assert wire.requests[1]["body"]["reasoning_effort"] == "xhigh"

    disabled_rule = deepcopy(edited_rule)
    disabled_rule["enabled"] = False
    await agent._request_rules.async_update(created["id"], disabled_rule)
    third = await _say(hass, agent, "continue after disabling", first.conversation_id)
    assert _speech(third) == "Disabled rule did not rewrite state."
    assert wire.requests[2]["body"]["model"] == "gpt-6-astra"
    assert wire.requests[2]["body"]["reasoning_effort"] == "xhigh"

    await agent._request_rules.async_delete(created["id"])
    fourth = await _say(hass, agent, "continue after deleting", first.conversation_id)
    assert _speech(fourth) == "Deleted rule did not rewrite state."
    assert wire.requests[3]["body"]["model"] == "gpt-6-astra"
    assert wire.requests[3]["body"]["reasoning_effort"] == "xhigh"


async def test_config_entry_reload_resets_transient_conversation_route(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A full agent reload is an explicit boundary for transient routing state."""
    entry = _make_entry(
        "Routing reload boundary",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "medium",
            CONF_FUNCTION_TOOLS: [],
            CONF_FUNCTION_GROUPS: [],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    await agent._request_rules.async_create(_rule())

    first_wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Conversation route established.")]
    )
    first = await _say(hass, agent, "use the deep route")
    assert _speech(first) == "Conversation route established."
    assert first_wire.requests[0]["body"]["model"] == "gpt-6-astra"
    assert first_wire.requests[0]["body"]["reasoning_effort"] == "xhigh"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reloaded = conversation.async_get_agent(hass, entry.entry_id)
    assert reloaded is not None
    assert reloaded is not agent

    second_wire = _install_wire(
        monkeypatch, reloaded, [_chat_sse_text("Configured defaults restored.")]
    )
    second = await _say(hass, reloaded, "continue after reload", first.conversation_id)
    assert _speech(second) == "Configured defaults restored."
    assert second_wire.requests[0]["body"]["model"] == "gpt-5.6"
    assert second_wire.requests[0]["body"]["reasoning_effort"] == "medium"
