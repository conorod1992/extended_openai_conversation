"""Shared inspection preserves genuine HA execution and preflight boundaries."""

from copy import deepcopy
import logging

import pytest

from custom_components.extended_openai_conversation_responses.functions.script import (
    ScriptFunction,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script, async_validate_actions_config
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_request_rules_script_semantics import _local

SHAPES = (
    "sequence",
    "if",
    "choose",
    "repeat",
    "parallel-mapping",
    "parallel-list",
    "parallel-sequences",
    "parallel-metadata",
    "choose-repeat",
)


def _shapes(shape, leaf):
    """Specify authored and native trees independently of production normalization."""
    if shape == "sequence":
        return {"sequence": leaf}, {"sequence": [leaf]}
    if shape == "if":
        return {"if": "{{ true }}", "then": leaf, "else": {"stop": "unused"}}, {
            "if": "{{ true }}",
            "then": [leaf],
            "else": [{"stop": "unused"}],
        }
    if shape == "choose":
        return {"choose": {"conditions": "{{ true }}", "sequence": leaf}}, {
            "choose": [{"conditions": "{{ true }}", "sequence": [leaf]}]
        }
    if shape == "repeat":
        return {"repeat": {"count": 2, "sequence": leaf}}, {
            "repeat": {"count": 2, "sequence": [leaf]}
        }
    if shape == "parallel-mapping":
        return {"parallel": leaf}, {"parallel": [{"sequence": [leaf]}]}
    if shape == "parallel-list":
        return {"parallel": [leaf]}, {"parallel": [{"sequence": [leaf]}]}
    if shape == "parallel-sequences":
        return {"parallel": [[leaf]]}, {"parallel": [{"sequence": [leaf]}]}
    if shape == "parallel-metadata":
        return {"parallel": [{"alias": "Branch", "sequence": leaf}]}, {
            "parallel": [{"alias": "Branch", "sequence": [leaf]}]
        }
    if shape == "choose-repeat":
        return {
            "choose": {
                "conditions": "{{ true }}",
                "sequence": {"repeat": {"count": 2, "sequence": leaf}},
            }
        }, {
            "choose": [
                {
                    "conditions": "{{ true }}",
                    "sequence": [{"repeat": {"count": 2, "sequence": [leaf]}}],
                }
            ]
        }
    raise AssertionError(shape)


async def _native(hass, actions):
    sequence = await async_validate_actions_config(
        hass, cv.SCRIPT_SCHEMA(deepcopy(actions))
    )
    script = Script(
        hass,
        sequence,
        "Native structural oracle",
        "structure_probe",
        logger=logging.getLogger(__name__),
    )
    try:
        await script.async_run()
    finally:
        await script.async_stop()


@pytest.mark.parametrize("shape", SHAPES)
async def test_authored_rule_and_script_function_match_independent_native_tree(
    hass, monkeypatch, shape
):
    calls = []

    async def record(call):
        calls.append(deepcopy(dict(call.data)))

    hass.services.async_register("structure_probe", "record", record)
    leaf = {
        "action": "structure_probe.record",
        "data": {
            "message": "{{ marker }}",
            "sequence": "opaque",
            "device_id": "payload",
        },
    }
    authored, canonical = _shapes(shape, leaf)
    variables = {"variables": {"marker": "literal-value"}}
    raw = [variables, authored]
    original = deepcopy(raw)
    expected = [
        {"message": "literal-value", "sequence": "opaque", "device_id": "payload"}
    ] * (2 if shape in {"repeat", "choose-repeat"} else 1)
    await _native(hass, [variables, canonical])
    assert calls == expected
    calls.clear()
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    await agent._request_rules.async_create(_local(raw))
    assert _speech(await _say(hass, agent, "run rule")) == "Done"
    assert calls == expected
    calls.clear()
    assert (
        await ScriptFunction().execute(
            hass, {"sequence": cv.SCRIPT_SCHEMA([variables, canonical])}, {}, None, []
        )
        == "Success"
    )
    assert calls == expected
    assert raw == original


@pytest.mark.parametrize("shape", SHAPES)
async def test_nested_device_preflight_prevents_even_preceding_service_effects(
    hass, shape
):
    calls = []

    async def record(_call):
        calls.append("effect")

    hass.services.async_register("structure_probe", "record", record)
    device = {
        "device_id": "0123456789abcdef0123456789abcdef",
        "domain": "light",
        "entity_id": "light.probe",
        "type": "turn_on",
    }
    _authored, canonical = _shapes(shape, device)
    sequence = cv.SCRIPT_SCHEMA([{"action": "structure_probe.record"}, canonical])
    with pytest.raises(HomeAssistantError, match="do not support device actions"):
        await ScriptFunction().execute(hass, {"sequence": sequence}, {}, None, [])
    assert calls == []


@pytest.mark.parametrize("limit", ["nodes", "depth"])
async def test_payload_budget_remains_stricter_than_native_ha_action_count(
    hass, monkeypatch, limit
):
    from custom_components.extended_openai_conversation_responses import request_rules

    calls = []

    async def record(call):
        calls.append(deepcopy(dict(call.data)))

    hass.services.async_register("structure_probe", "record", record)
    payload = [None] * request_rules.MAX_SCRIPT_NODES
    if limit == "depth":
        payload = "leaf"
        for _ in range(request_rules.MAX_SCRIPT_DEPTH + 1):
            payload = {"nested": payload}
    actions = [{"action": "structure_probe.record", "data": {"payload": payload}}]
    await _native(hass, actions)
    assert calls == [{"payload": payload}]
    calls.clear()
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    assert len(list(request_rules._iter_script_actions(actions))) == 1
    with pytest.raises(
        ValueError, match="nodes" if limit == "nodes" else "maximum depth"
    ):
        await agent._request_rules.async_create(_local(actions))
    assert calls == []
