"""Native HA condition equivalence and Function result data-flow contracts.

These tests deliberately use independent JSON traversal as their oracle, then
observe the actual HA service arguments and public Assist result.
"""
from __future__ import annotations

import json

import pytest

from custom_components.extended_openai_conversation_responses.const import CONF_FUNCTION_TOOLS, DOMAIN, SERVICE_CALL_FUNCTION
from homeassistant.helpers import condition as ha_condition, config_validation as cv
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_request_rules_script_semantics import _local, _record_action


@pytest.mark.parametrize(
    "condition,entity_state,expected",
    [
        ({"condition": "template", "value_template": "{{ is_state('input_boolean.probe', 'on') }}"}, "on", True),
        ({"condition": "template", "value_template": "{{ is_state('input_boolean.probe', 'on') }}"}, "off", False),
        ({"condition": "state", "entity_id": "input_boolean.probe", "state": "on"}, "on", True),
        ({"condition": "state", "entity_id": "input_boolean.probe", "state": "on"}, "off", False),
        ({"condition": "and", "conditions": [
            {"condition": "template", "value_template": "{{ true }}"},
            {"condition": "state", "entity_id": "input_boolean.probe", "state": "on"},
        ]}, "on", True),
        ({"condition": "not", "conditions": [
            {"condition": "template", "value_template": "{{ false }}"},
        ]}, "on", True),
    ],
    ids=["template-true", "template-false", "state-true", "state-false",
         "nested-and", "nested-not"],
)
async def test_saved_rule_condition_matches_native_ha_execution(
    hass, monkeypatch, condition, entity_state, expected
):
    """Save/reload and execute; schema acceptance is not enough."""
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    calls = []
    async def record(call):
        calls.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    hass.states.async_set("input_boolean.probe", entity_state)

    validated = cv.CONDITION_SCHEMA(condition.copy())
    validated = await ha_condition.async_validate_condition_config(hass, validated)
    checker = await ha_condition.async_from_config(hass, validated)
    assert bool(checker.async_check(variables={})) is expected

    rule = _local([_record_action("condition-matched")], phrase="check condition",
                  success="Matched.")
    rule["conditions"] = [condition]
    await agent._request_rules.async_create(rule)
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    refreshed = await _agent_after_reload(hass, agent.entry.entry_id)
    result = await _say(hass, refreshed, "check condition")
    if expected:
        assert _speech(result) == "Matched."
        assert calls == ["condition-matched"]
    else:
        assert calls == []


async def _agent_after_reload(hass, entry_id):
    from homeassistant.components import conversation
    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None
    return agent


# A deliberately independent oracle: no production lookup/templating helpers.
def _lookup(value, path):
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(part)
    return current


_DATA = {
    "reading": {
        "rows": [{"level": 73, "enabled": False}, {"level": 0, "enabled": True}],
        "map": {"0": {"level": 84}},
        "items": [{"value": ""}],
        "none": None,
    }
}


@pytest.mark.parametrize(
    "path",
    [
        "reading.rows.0.level",
        "reading.rows.1.level",
        "reading.rows.0.enabled",
        "reading.rows.1.enabled",
        "reading.map.0.level",
        "reading.items.0.value",
        "reading.none",
    ],
)
async def test_result_path_agrees_across_later_action_and_spoken_response(
    hass, monkeypatch, path
):
    """A valid result reference must resolve identically in both destinations."""
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [{
        "spec": {"name": "produce_reading", "description": "Nested JSON",
                 "parameters": {"type": "object", "properties": {}}},
        "function": {"type": "template", "value_template": json.dumps(_DATA)},
    }]})
    _provider(monkeypatch, agent, [])
    calls = []
    async def record(call):
        calls.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    alias = "payload"
    reference = "{" + alias + "." + path + "}"
    capture = {"action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
               "data": {"function": "produce_reading", "arguments": {}, "result_alias": alias}}
    await agent._request_rules.async_create(
        _local([capture, _record_action(reference)], success="Result: " + reference)
    )
    expected = _lookup(_DATA, path)
    result = await _say(hass, agent, "run rule")
    assert _speech(result) == f"Result: {expected}"
    assert calls == [expected if isinstance(expected, str) else str(expected).lower()
                     if isinstance(expected, bool) else str(expected)]


@pytest.mark.parametrize("missing_path", ["reading.absent", "reading.rows.99.level", "reading.rows.0.absent"])
async def test_invalid_result_path_never_executes_dependent_action(
    hass, monkeypatch, missing_path
):
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [{
        "spec": {"name": "produce_reading", "description": "Nested JSON",
                 "parameters": {"type": "object", "properties": {}}},
        "function": {"type": "template", "value_template": json.dumps(_DATA)},
    }]})
    _provider(monkeypatch, agent, [])
    calls = []
    async def record(call):
        calls.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    capture = {"action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
               "data": {"function": "produce_reading", "arguments": {}, "result_alias": "payload"}}
    await agent._request_rules.async_create(_local(
        [capture, _record_action("{payload." + missing_path + "}")],
        failure="Failed safely",
    ))
    result = await _say(hass, agent, "run rule")
    assert _speech(result, successful=False) == "Failed safely"
    assert calls == []


@pytest.mark.parametrize(
    "field,expected",
    [("rows.0.level", "73"), ("rows.1.level", "0"),
     ("map.0.level", "84"), ("rows.0.enabled", "False")],
)
async def test_generated_two_step_dataflow_preserves_nested_value(
    hass, monkeypatch, field, expected
):
    """Producer → HA variable → HA service → final response, without resetting state."""
    agent = await _agent(hass, **{CONF_FUNCTION_TOOLS: [{
        "spec": {"name": "produce_reading", "description": "Nested JSON",
                 "parameters": {"type": "object", "properties": {}}},
        "function": {"type": "template", "value_template": json.dumps(_DATA)},
    }]})
    _provider(monkeypatch, agent, [])
    seen = []
    async def record(call):
        seen.append(call.data["message"])
    hass.services.async_register("rule_probe", "record", record)
    capture = {"action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
               "data": {"function": "produce_reading", "arguments": {}, "result_alias": "payload"}}
    reference = "{payload.reading." + field + "}"
    await agent._request_rules.async_create(_local(
        [capture, {"variables": {"intermediate": reference}},
         _record_action("{{ intermediate }}")],
        success="Result: " + reference,
    ))
    result = await _say(hass, agent, "run rule")
    assert _speech(result) == "Result: " + expected
    assert seen == [expected]
