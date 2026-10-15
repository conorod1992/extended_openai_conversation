"""Execution-sensitive rewriting compared with genuine native HA behavior."""

import asyncio
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    async_evaluate_rule,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_request_rules_script_semantics import _local
from tests_real_ha.test_script_structure_inspection import _native, _shapes


@pytest.mark.parametrize(
    "shape", ["sequence", "if", "choose", "repeat", "parallel-metadata"]
)
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("error", [False, True])
async def test_nested_enabled_stop_matches_native_effects_and_rule_outcome(
    hass, monkeypatch, shape, enabled, error
):
    calls = []

    async def record(call):
        calls.append(call.data["message"])

    hass.services.async_register("rewrite_probe", "record", record)
    stop = {
        "stop": "finished",
        "enabled": "{{ true }}" if enabled else "{{ false }}",
        "error": error,
        "alias": "Authored Stop",
        "continue_on_error": False,
    }
    authored, canonical = _shapes(shape, stop)
    before = {"action": "rewrite_probe.record", "data": {"message": "before"}}
    after = {"action": "rewrite_probe.record", "data": {"message": "after"}}
    raw = [before, authored, after]
    original = deepcopy(raw)
    try:
        await _native(hass, [before, canonical, after])
    except HomeAssistantError:
        assert enabled and error
    baseline = list(calls)
    assert baseline[0] == "before"
    if not enabled:
        assert baseline == ["before", "after"]
    calls.clear()
    agent = await _agent(hass)
    _provider(monkeypatch, agent, [])
    await agent._request_rules.async_create(_local(raw))
    result = await _say(hass, agent, "run rule")
    successful = not (enabled and error)
    assert _speech(result, successful=successful) == (
        "Done" if successful else "Failed safely"
    )
    assert calls == baseline
    assert raw == original


@pytest.mark.parametrize("condition", ["while", "until"])
@pytest.mark.parametrize(
    "mode, hidden_effect",
    [
        ("guest", False),
        ("guest", True),
        ("revoked", False),
        ("revoked", True),
        ("owner", True),
    ],
)
async def test_live_guard_and_template_policy_after_await_preserve_trusted_conditions(
    hass, condition, mode, hidden_effect
):
    agent = await _agent(hass)
    assert await async_setup_component(
        hass, "input_text", {"input_text": {"rewrite_target": {"initial": "initial"}}}
    )
    public, private = "input_text.rewrite_target", "sensor.rewrite_private"
    canary = "private-rewrite-canary"
    hass.states.async_set(private, canary)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(_call):
        entered.set()
        await release.wait()

    hass.services.async_register("rewrite_probe", "pause", pause)
    trusted = "is_state('" + private + "', '" + canary + "')"
    condition_template = (
        "{{ "
        + trusted
        + (
            " and repeat.index <= 1"
            if condition == "while"
            else " and repeat.index >= 1"
        )
        + " }}"
    )
    effect = {
        "action": "input_text.set_value",
        "target": {"entity_id": public},
        "data": {
            "value": "{{ states('" + private + "') }}" if hidden_effect else "allowed"
        },
    }
    actions = [
        {"action": "rewrite_probe.pause"},
        {
            "choose": [
                {
                    "conditions": "{{ " + trusted + " }}",
                    "sequence": [
                        {
                            "repeat": {
                                condition: condition_template,
                                "sequence": [effect],
                            }
                        }
                    ],
                }
            ]
        },
    ]
    await agent._request_rules.async_create(_local(actions))
    policy = GuestCapabilityPolicy.unrestricted()
    task = asyncio.create_task(
        async_evaluate_rule(
            hass,
            agent._request_rules,
            RequestRuleRuntime(),
            "run rule",
            "session",
            guest_policy=policy,
            live_guest_policy=lambda: policy,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if mode != "owner":
            policy = GuestCapabilityPolicy(
                True,
                readable_entity_ids=frozenset({public}),
                controllable_entity_ids=frozenset()
                if mode == "revoked"
                else frozenset({public}),
            )
        release.set()
        result = await asyncio.wait_for(task, 5)
        allowed = mode == "owner" or (mode == "guest" and not hidden_effect)
        assert result is not None and result.successful is allowed
        expected = canary if mode == "owner" else "allowed" if allowed else "initial"
        assert hass.states.get(public).state == expected
        if not allowed:
            assert canary not in result.response
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
