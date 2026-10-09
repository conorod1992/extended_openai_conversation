"""Dependent execution journeys through a live Home Assistant instance.

No mutation of the integration's execution implementation: provider replies are
scripted at the SDK transport edge and effects are real HA service calls.
"""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES, CONF_API_MODE,
    CONF_CHAT_MODEL, CONF_FUNCTION_TOOLS, DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.components import ai_task, conversation
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import (
    _agent, _rule, _say, _speech,
)
from tests_real_ha.test_ai_task_runtime import FakeClient
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text, _chat_sse_tool_call, _install_wire,
    _responses_sse_text,
)
from tests_real_ha.test_request_rules_script_semantics import _local, _record_action


def _batch(calls):
    """One real Chat Completions stream chunk containing several tool calls."""
    chunk = {
        "id": "chatcmpl-batch", "object": "chat.completion.chunk",
        "created": 0, "model": "gpt-5.6",
        "choices": [{"index": 0, "delta": {
            "role": "assistant", "tool_calls": [
                {"index": index, "id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments)}}
                for index, (call_id, name, arguments) in enumerate(calls)
            ]}, "finish_reason": "tool_calls"}],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


async def test_batch_distinct_call_ids_and_results_then_followup(hass, monkeypatch):
    """Several calls share one provider response but retain separate acknowledgements."""
    tool = {
        "spec": {"name": "echo_probed", "description": "Echo a distinguishable value",
                 "parameters": {"type": "object", "properties": {
                     "value": {"type": "string"}}}},
        "function": {"type": "template", "value_template": "{{ value }}"},
    }
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [tool]})
    wire = _install_wire(monkeypatch, agent, [
        _batch([
            ("first", "echo_probed", {"value": "alpha"}),
            ("second", "echo_probed", {"value": "beta"}),
        ]),
        _chat_sse_text("Both acknowledged."),
        _chat_sse_text("Followup healthy."),
    ])
    first = await _say(hass, agent, "Echo alpha and beta")
    assert _speech(first) == "Both acknowledged."
    assert len(wire.requests) == 2
    request = wire.requests[1]["body"]
    tool_messages = [item for item in request["messages"] if item["role"] == "tool"]
    assert [item["tool_call_id"] for item in tool_messages] == ["first", "second"]
    assert "alpha" in tool_messages[0]["content"]
    assert "beta" in tool_messages[1]["content"]
    second = await _say(hass, agent, "Continue", first.conversation_id)
    assert _speech(second) == "Followup healthy."
    assert len(wire.requests) == 3


async def test_edit_working_rule_then_execute_again_without_stale_alias(hass, monkeypatch):
    """Rule editing changes execution rather than just its saved representation."""
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [{
        "spec": {"name": "probe_reading", "description": "Return reading",
                 "parameters": {"type": "object", "properties": {}}},
        "function": {"type": "template", "value_template": '{"rows":[{"level":73}]}'},
    }]})
    seen = []
    async def record(call):
        seen.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    _install_wire(monkeypatch, agent, [])
    capture = {"action": "extended_openai_conversation_responses.call_function",
               "data": {"function": "probe_reading", "arguments": {},
                        "result_alias": "reading", "step_id": "reading-step"}}
    created = await agent._request_rules.async_create(_local(
        [capture, _record_action("before={reading.rows.0.level}")],
        success="First {reading.rows.0.level}",
    ))
    assert _speech(await _say(hass, agent, "run rule")) == "First 73"
    assert seen == ["before=73"]
    modified = deepcopy(created)
    modified["action"]["actions"][0]["data"]["result_alias"] = "updated"
    modified["action"]["actions"][1]["data"]["message"] = "after={updated.rows.0.level}"
    modified["action"]["success_response"] = "Second {updated.rows.0.level}"
    await agent._request_rules.async_update(created["id"], modified)
    assert _speech(await _say(hass, agent, "run rule")) == "Second 73"
    assert seen == ["before=73", "after=73"]


async def test_nested_ai_task_service_then_rule_continues(hass, monkeypatch):
    """An AI Task invoked by an outer Request Rule must not consume its state."""
    entry = _make_entry(
        "Nested AI Task",
        include_ai_task=True,
        conversation_options={CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
                              CONF_CHAT_MODEL: "gpt-5.6"},
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    from homeassistant.helpers import entity_registry as er
    subentry = next(x for x in entry.subentries.values() if x.subentry_type == "ai_task_data")
    entity_id = er.async_get(hass).async_get_entity_id(
        "ai_task", "extended_openai_conversation_responses", subentry.subentry_id)
    assert entity_id is not None
    entry.runtime_data = FakeClient(["INNER_RESULT"])
    seen = []
    async def record(call):
        seen.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    await agent._request_rules.async_create(_local([
        {"action": "ai_task.generate_data",
         "data": {"entity_id": entity_id, "task_name": "Nested request",
                  "instructions": "Return inner result"},
         "response_variable": "nested"},
        _record_action("{{ nested.data }}"),
    ], success="Outer completed."))
    result = await _say(hass, agent, "run rule")
    assert _speech(result) == "Outer completed."
    assert seen == ["INNER_RESULT"]


async def test_reused_rule_condition_observes_changed_state_and_edit(hass, monkeypatch):
    agent = await _agent(hass)
    _install_wire(monkeypatch, agent, [])
    seen = []
    async def record(call):
        seen.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    conditional = _local([_record_action("conditional")],
                         phrase="test current state", success="Conditional.")
    conditional["conditions"] = [{
        "condition": "template",
        "value_template": "{{ is_state('input_boolean.probe', 'on') }}",
    }]
    first = await agent._request_rules.async_create(conditional)
    fallback = _local([_record_action("fallback")],
                      phrase="test current state", success="Fallback.")
    fallback["order"] = 1
    await agent._request_rules.async_create(fallback)
    for state, response in [("on", "Conditional."), ("off", "Fallback."),
                            ("on", "Conditional.")]:
        hass.states.async_set("input_boolean.probe", state)
        assert _speech(await _say(hass, agent, "test current state")) == response
    updated = deepcopy(first)
    updated["conditions"] = [{
        "condition": "template",
        "value_template": "{{ is_state('input_boolean.probe', 'off') }}",
    }]
    await agent._request_rules.async_update(first["id"], updated)
    assert _speech(await _say(hass, agent, "test current state")) == "Fallback."
    hass.states.async_set("input_boolean.probe", "off")
    assert _speech(await _say(hass, agent, "test current state")) == "Conditional."
    assert seen == ["conditional", "fallback", "conditional", "fallback", "conditional"]


async def test_api_mode_switch_after_existing_tool_history_remains_usable(hass, monkeypatch):
    """A previously used conversation is safe across both provider formats."""
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])]})
    calls = []
    async def record(call):
        calls.append(call.data["marker"])
    hass.services.async_register("sequence_probe", "record", record)
    first_wire = _install_wire(monkeypatch, agent, [
        _chat_sse_tool_call(
            "earlier-action", "execute_services",
            {"list": [{"domain": "sequence_probe", "service": "record",
                       "service_data": {"marker": "once"}}]}),
        _chat_sse_text("Initial completed."),
    ])
    first = await _say(hass, agent, "Perform initial action")
    assert _speech(first) == "Initial completed."
    assert calls == ["once"]
    assert len(first_wire.requests) == 2
    entry, subentry = agent.entry, agent.subentry
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, CONF_API_MODE: API_MODE_RESPONSES})
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    wire = _install_wire(monkeypatch, agent, [_responses_sse_text("Switched safely.")])
    later = await _say(hass, agent, "Continue after switch", first.conversation_id)
    assert _speech(later) == "Switched safely."
    assert wire.requests[0]["path"] == "/v1/responses"
    assert calls == ["once"]
