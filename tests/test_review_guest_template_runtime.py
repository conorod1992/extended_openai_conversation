"""Guest reads use actual native variables and state branches at consumption."""

from pathlib import Path

import pytest

from homeassistant.core import HomeAssistant

from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    async_evaluate_rule,
)
from tests.test_request_rules import local_rule, manager


async def test_guest_native_variable_cannot_retarget_captured_read(tmp_path: Path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.public", "PUBLIC")
    hass.states.async_set("sensor.private", "SECRET")
    policy = GuestCapabilityPolicy(
        guest_active=True, readable_entity_ids=frozenset({"sensor.public"})
    )
    rule = local_rule(phrases=["read {entity}"], match_type="sentence_pattern")
    reader = "{{ states(entity) }}"
    consumer = {"set_conversation_response": reader}
    rule["action"]["actions"] = [{"variables": {"entity": "sensor.private"}}, consumer]
    try:
        result = await async_evaluate_rule(
            hass,
            await manager(rule),
            RequestRuleRuntime(),
            "read sensor.public",
            "guest",
            guest_policy=policy,
            live_guest_policy=lambda: policy,
        )
        assert not result.successful
        assert result.response == GUEST_MODE_UNAVAILABLE
    finally:
        await hass.async_stop(force=True)


async def test_guest_state_branch_rechecked_after_prior_native_step(tmp_path: Path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.public", "off")
    hass.states.async_set("sensor.private", "SECRET")
    policy = GuestCapabilityPolicy(
        guest_active=True, readable_entity_ids=frozenset({"sensor.public"})
    )
    rule = local_rule(phrases=["read"])
    rule["action"]["actions"] = [
        {
            "set_conversation_response": "{% if is_state('sensor.public', 'on') %}{{ states('sensor.private') }}{% else %}PUBLIC{% endif %}"
        }
    ]

    # The live policy callback runs after preflight and simulates state changing
    # while native actions or waits are in progress.
    def live_policy():
        hass.states.async_set("sensor.public", "on")
        return policy

    try:
        result = await async_evaluate_rule(
            hass,
            await manager(rule),
            RequestRuleRuntime(),
            "read",
            "guest",
            guest_policy=policy,
            live_guest_policy=live_policy,
        )
        assert not result.successful
        assert result.response == GUEST_MODE_UNAVAILABLE
    finally:
        await hass.async_stop(force=True)
