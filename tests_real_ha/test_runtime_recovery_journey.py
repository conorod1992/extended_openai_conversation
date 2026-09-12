"""Real-HA acceptance for multi-turn degradation and runtime recovery."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    CONF_FUNCTION_TOOLS,
    CONVERSATION_CONTINUITY_USER,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockUser

from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_function_execution_composition import (
    _EntityActionTool,
    _MutableAPI,
    _ha_reference,
    _responses_sse_tool_calls,
)
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _responses_sse_tool_call,
    _tool_names,
)
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _responses_sse_text,
    _speech,
)

_OWNER_ID = "dirty-session-recovery-owner"
_OWNER_SCOPE = f"user:{_OWNER_ID}"
_GROUP_ID = "recovery-status-group"
_GROUP_TOOL = "recovery_status"
_HA_ALIAS = "ha_recovery_entity_action"
_INITIAL_RESULT = "recovery-before-reload"
_UPDATED_RESULT = "recovery-after-reload"


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
    *,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public conversation API as one stable owner."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=_OWNER_ID),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _group_tool(result: str) -> dict[str, Any]:
    return {
        "spec": {
            "name": _GROUP_TOOL,
            "description": "Return the deterministic runtime-recovery status.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": result},
        "enabled": True,
    }


def _group() -> dict[str, Any]:
    return {
        "id": _GROUP_ID,
        "name": "Recovery status",
        "description": "Load the runtime-recovery status capability.",
        "loading_mode": "on_demand",
        "functions": [_GROUP_TOOL],
        "enabled": True,
    }


async def test_dirty_session_recovers_across_ha_failure_config_mutation_and_reload(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A failed middle turn must not poison continuity, history, or rebuilt tool state."""
    MockUser(id=_OWNER_ID, name="Dirty Session Recovery Owner").add_to_hass(hass)

    entity_a = "sensor.recovery_valid_a"
    entity_b = "sensor.recovery_stale_b"
    entity_c = "sensor.recovery_valid_c"
    for entity_id in (entity_a, entity_b, entity_c):
        hass.states.async_set(entity_id, "ready")
    await hass.async_block_till_done()

    ha_tool = _EntityActionTool()
    api = _MutableAPI(hass, ha_tool)
    llm.async_register_api(hass, api)
    saved_ha_tool = {
        "spec": {"name": _HA_ALIAS},
        "function": _ha_reference(ha_tool, api),
        "enabled": True,
    }
    entry = _make_entry(
        "Dirty Session Runtime Recovery",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_FUNCTION_TOOL_ERROR_RECOVERY: True,
            CONF_FUNCTION_TOOLS: [_group_tool(_INITIAL_RESULT), saved_ha_tool],
            CONF_FUNCTION_GROUPS: [_group()],
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    initial_runtime = agent._function_groups_runtime
    initial_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_call(
                "call-load-recovery-initial",
                "load_function_groups",
                {"groups": [_GROUP_ID]},
            ),
            _responses_sse_tool_call(
                "call-recovery-status-initial",
                _GROUP_TOOL,
                {},
            ),
            _responses_sse_text("Initial recovery state established."),
        ],
    )
    first = await _say(hass, agent, "Load and check my recovery status")

    assert _speech(first) == "Initial recovery state established."
    assert len(initial_wire.requests) == 3
    assert _GROUP_TOOL not in _tool_names(
        initial_wire.requests[0]["body"], API_MODE_RESPONSES
    )
    assert _GROUP_TOOL in _tool_names(
        initial_wire.requests[1]["body"], API_MODE_RESPONSES
    )
    assert _INITIAL_RESULT in _serialized(initial_wire.requests[2]["body"])
    assert initial_runtime is not None
    assert initial_runtime.stats()["loaded_function_groups"] == [_GROUP_ID]

    continuity = agent._continuity
    assert continuity is not None
    active_before_failure = continuity._sessions[_OWNER_SCOPE]
    history_before_failure = deepcopy(active_before_failure.history)
    assert active_before_failure.in_flight is False

    failing_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_calls(
                [
                    ("call-recovery-valid-a", _HA_ALIAS, {"entity_id": entity_a}),
                    ("call-recovery-stale-b", _HA_ALIAS, {"entity_id": entity_b}),
                    ("call-recovery-valid-c", _HA_ALIAS, {"entity_id": entity_c}),
                ]
            )
        ],
    )
    hass.states.async_remove(entity_b)
    await hass.async_block_till_done()

    failed = await _say(
        hass,
        agent,
        "Run the ordered recovery entity actions",
        conversation_id=first.conversation_id,
    )

    assert failed.response.error_code is not None
    failed_speech = failed.response.as_dict()["speech"]["plain"]["speech"]
    assert entity_b in failed_speech
    assert "unavailable" in failed_speech
    assert ha_tool.attempts == [entity_a, entity_b]
    assert ha_tool.calls == [entity_a]
    assert len(failing_wire.requests) == 1

    # The failed turn must release its continuity claim and must not replace the last
    # known-good history with a half-finished provider/tool exchange.
    active_after_failure = continuity._sessions[_OWNER_SCOPE]
    assert active_after_failure.in_flight is False
    assert active_after_failure.claim_token is None
    assert active_after_failure.history == history_before_failure

    conversation_subentry = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )
    updated_data = dict(conversation_subentry.data)
    updated_tools = []
    for tool in updated_data[CONF_FUNCTION_TOOLS]:
        if tool["spec"]["name"] != _GROUP_TOOL:
            updated_tools.append(dict(tool))
            continue
        updated = deepcopy(tool)
        updated["function"]["value_template"] = _UPDATED_RESULT
        updated_tools.append(updated)
    updated_data[CONF_FUNCTION_TOOLS] = updated_tools

    # Mutate persisted configuration only after the degraded turn, then rebuild the
    # real agent through HA's unload/setup lifecycle.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.config_entries.async_update_subentry(
        entry,
        conversation_subentry,
        data=updated_data,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    reloaded = conversation.async_get_agent(hass, entry.entry_id)
    assert reloaded is not None
    assert reloaded is not agent
    assert reloaded._continuity is continuity
    assert reloaded._function_groups_runtime is not initial_runtime
    assert reloaded._function_groups_runtime.stats()["active_function_group_sessions"] == 0

    recovery_wire = _install_wire(
        monkeypatch,
        reloaded,
        [
            _responses_sse_tool_call(
                "call-load-recovery-updated",
                "load_function_groups",
                {"groups": [_GROUP_ID]},
            ),
            _responses_sse_tool_call(
                "call-recovery-status-updated",
                _GROUP_TOOL,
                {},
            ),
            _responses_sse_text("Recovery completed with updated configuration."),
        ],
    )
    recovered = await _say(hass, reloaded, "Check recovery status after reload")

    assert recovered.conversation_id == first.conversation_id
    assert _speech(recovered) == "Recovery completed with updated configuration."
    assert len(recovery_wire.requests) == 3

    first_recovery_request = recovery_wire.requests[0]["body"]
    first_recovery_text = _serialized(first_recovery_request)
    # Continuity survived the reload, but failed-turn provider/tool artifacts did not.
    assert "Initial recovery state established." in first_recovery_text
    assert "call-recovery-stale-b" not in first_recovery_text
    assert "call-recovery-valid-c" not in first_recovery_text
    assert entity_b not in first_recovery_text
    # Function-group runtime state is intentionally transient, so reload requires an
    # explicit fresh load rather than inheriting the old session's loaded group.
    assert _GROUP_TOOL not in _tool_names(first_recovery_request, API_MODE_RESPONSES)
    assert "load_function_groups" in _tool_names(
        first_recovery_request, API_MODE_RESPONSES
    )
    assert _GROUP_TOOL in _tool_names(
        recovery_wire.requests[1]["body"], API_MODE_RESPONSES
    )
    assert _UPDATED_RESULT in _serialized(recovery_wire.requests[2]["body"])
    assert _INITIAL_RESULT not in _serialized(recovery_wire.requests[2]["body"])

    final_session = continuity._sessions[_OWNER_SCOPE]
    assert final_session.in_flight is False
    assert final_session.claim_token is None
    assert ha_tool.attempts == [entity_a, entity_b]
    assert ha_tool.calls == [entity_a]
