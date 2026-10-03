"""Live Guest authorization when a native Request Rule resumes."""

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_GUEST_EXCLUDED_ENTITIES,
    CONF_GUEST_POLICY_VERSION,
    GUEST_POLICY_VERSION,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
)
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_request_rules_script_semantics import _local
from tests_stress.conftest import record


@pytest.mark.parametrize(
    "transition", ["activate", "tighten", "deactivate", "activate_then_disable"]
)
async def test_waiting_native_rule_obeys_live_guest_restrictions(
    hass, monkeypatch, stress_trace, transition
):
    agent = await _agent(
        hass,
        **{
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_EXCLUDED_ENTITIES: ["light.pending"]
            if transition.startswith("activate")
            else [],
        },
    )
    _provider(monkeypatch, agent, [])
    for entity_id in ["light.before", "light.pending", "light.healthy"]:
        hass.states.async_set(entity_id, "on")
        async_expose_entity(hass, "conversation", entity_id, True)
    entered = asyncio.Event()
    second_wait = asyncio.Event()
    effects = []

    async def turn_off(call):
        entity_id = call.data["entity_id"]
        if isinstance(entity_id, list):
            entity_id = entity_id[0]
        effects.append(entity_id)
        hass.states.async_set(entity_id, "off")
        if entity_id == "light.before":
            entered.set()
        if entity_id == "light.healthy":
            second_wait.set()

    hass.services.async_register("light", "turn_off", turn_off)
    if not transition.startswith("activate"):
        await agent._guest_mode.async_update_trusted(indefinite=True)

    def action(entity):
        return {"action": "light.turn_off", "target": {"entity_id": entity}}

    hass.states.async_set("sensor.rule_gate", "closed")
    await agent._request_rules.async_create(
        _local(
            [
                action("light.before"),
                {
                    "wait_template": "{{ is_state('sensor.rule_gate', 'open') }}",
                    "timeout": 10,
                    "continue_on_timeout": False,
                },
                *(
                    [
                        action("light.healthy"),
                        {
                            "wait_template": "{{ is_state('sensor.second_gate', 'open') }}",
                            "timeout": 10,
                            "continue_on_timeout": False,
                        },
                    ]
                    if transition == "activate_then_disable"
                    else []
                ),
                {"repeat": {"count": 1, "sequence": [action("light.pending")]}},
            ]
        )
    )
    await agent._request_rules.async_create(
        _local([action("light.healthy")], phrase="healthy rule")
    )
    running = asyncio.create_task(_say(hass, agent, "run rule"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await hass.async_block_till_done()
        assert not running.done()
        if transition.startswith("activate"):
            await agent._guest_mode.async_update_trusted(indefinite=True)
        elif transition == "tighten":
            hass.config_entries.async_update_subentry(
                agent.entry,
                agent.subentry,
                data={
                    **agent.subentry.data,
                    CONF_GUEST_EXCLUDED_ENTITIES: ["light.pending"],
                },
            )
        else:
            await agent._guest_mode.async_disable_trusted()
        hass.states.async_set("sensor.rule_gate", "open")
        if transition == "activate_then_disable":
            await asyncio.wait_for(second_wait.wait(), 5)
            await hass.async_block_till_done()
            assert not running.done()
            await agent._guest_mode.async_disable_trusted()
            hass.states.async_set("sensor.second_gate", "open")
        response = await asyncio.wait_for(running, 5)
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    denied = transition != "deactivate"
    assert _speech(response) == (GUEST_MODE_UNAVAILABLE if denied else "Done")
    assert effects == (
        ["light.before", "light.healthy"]
        if transition == "activate_then_disable"
        else ["light.before"]
        if denied
        else ["light.before", "light.pending"]
    )
    assert hass.states.get("light.pending").state == ("on" if denied else "off")
    await hass.async_block_till_done()
    assert _speech(await _say(hass, agent, "healthy rule")) == "Done"
    assert effects.count("light.before") == 1
    assert effects[-1] == "light.healthy"
    record(
        stress_trace,
        "native_rule_guest_transition",
        transition=transition,
        effects=effects,
        denied=denied,
    )
