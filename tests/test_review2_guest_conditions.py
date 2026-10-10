"""Trusted native conditions retain controlled Guest outcomes."""

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


@pytest.mark.parametrize("kind", ["condition", "if", "choose", "until", "while"])
async def test_guest_native_conditions_can_read_private_state(tmp_path, kind):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "occupied")
    condition = {
        "condition": "template",
        "value_template": "{{ is_state('sensor.private', 'occupied') }}",
    }
    response = {"set_conversation_response": "Controlled answer"}
    if kind == "condition":
        actions = [condition, response]
    elif kind == "if":
        actions = [{"if": [condition], "then": [response]}]
    elif kind == "choose":
        actions = [{"choose": [{"conditions": [condition], "sequence": [response]}]}]
    elif kind == "until":
        actions = [{"repeat": {"until": [condition], "sequence": [response]}}]
    else:
        actions = [
            {
                "repeat": {
                    "while": [
                        {
                            **condition,
                            "value_template": "{{ is_state('sensor.private', 'empty') }}",
                        }
                    ],
                    "sequence": [],
                }
            },
            response,
        ]
    rule = local_rule(phrases=["status"])
    rule["action"]["actions"] = actions
    policy = GuestCapabilityPolicy(True, readable_entity_ids=frozenset())
    try:
        result = await async_evaluate_rule(
            hass,
            await manager(rule),
            RequestRuleRuntime(),
            "status",
            "guest",
            guest_policy=policy,
            live_guest_policy=lambda: policy,
        )
        assert result.successful
        assert result.response == "Controlled answer"
    finally:
        await hass.async_stop(force=True)


async def test_trusting_conditions_does_not_trust_response_templates(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "SECRET")
    rule = local_rule(phrases=["status"])
    rule["action"]["actions"] = [
        {
            "if": [
                {
                    "condition": "template",
                    "value_template": "{{ is_state('sensor.private', 'SECRET') }}",
                }
            ],
            "then": [{"set_conversation_response": "{{ states('sensor.private') }}"}],
        }
    ]
    policy = GuestCapabilityPolicy(True, readable_entity_ids=frozenset())
    try:
        result = await async_evaluate_rule(
            hass,
            await manager(rule),
            RequestRuleRuntime(),
            "status",
            "guest",
            guest_policy=policy,
            live_guest_policy=lambda: policy,
        )
        assert not result.successful
        assert result.response == GUEST_MODE_UNAVAILABLE
    finally:
        await hass.async_stop(force=True)


async def test_trusted_condition_cannot_authorize_private_effect(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "occupied")
    hass.states.async_set("light.private", "off")
    effects = []
    hass.services.async_register(
        "homeassistant", "update_entity", lambda call: effects.append(call)
    )
    rule = local_rule(phrases=["status"])
    rule["action"]["actions"] = [
        {
            "if": [
                {
                    "condition": "template",
                    "value_template": "{{ is_state('sensor.private', 'occupied') }}",
                }
            ],
            "then": [
                {
                    "action": "homeassistant.update_entity",
                    "target": {"entity_id": "light.private"},
                }
            ],
        }
    ]
    policy = GuestCapabilityPolicy(
        True, readable_entity_ids=frozenset(), controllable_entity_ids=frozenset()
    )
    try:
        result = await async_evaluate_rule(
            hass,
            await manager(rule),
            RequestRuleRuntime(),
            "status",
            "guest",
            guest_policy=policy,
            live_guest_policy=lambda: policy,
        )
        assert not result.successful
        assert result.response == GUEST_MODE_UNAVAILABLE
        assert not effects
    finally:
        await hass.async_stop(force=True)


async def test_service_data_named_condition_is_still_checked(tmp_path):
    from custom_components.extended_openai_conversation_responses.request_rules import (
        _guest_script_allowed,
    )

    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "SECRET")
    policy = GuestCapabilityPolicy(True, readable_entity_ids=frozenset())
    try:
        assert not _guest_script_allowed(
            hass,
            [
                {
                    "action": "homeassistant.update_entity",
                    "target": {"entity_id": "light.public"},
                    "data": {
                        "condition": "template",
                        "value_template": "{{ states('sensor.private') }}",
                    },
                }
            ],
            policy,
        )
    finally:
        await hass.async_stop(force=True)
