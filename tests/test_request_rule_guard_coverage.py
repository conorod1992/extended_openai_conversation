"""Stable coverage for Request Rule guard and Guest Mode helper branches."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import request_rules
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_CALL_FUNCTION,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from homeassistant.exceptions import HomeAssistantError


def test_guard_native_actions_instruments_nested_effects_and_preserves_enabled() -> (
    None
):
    actions = [
        {
            "choose": [
                {
                    "conditions": [],
                    "sequence": [
                        {
                            "action": "light.turn_on",
                            "target": {"entity_id": "light.kitchen"},
                            "enabled": False,
                        }
                    ],
                }
            ],
            "default": [{"scene": "scene.evening"}],
        },
        {"variables": {"unchanged": True}},
    ]

    guarded = request_rules._guard_native_actions(actions)

    nested = guarded[0]["choose"][0]["sequence"]
    assert nested[0] == {
        "action": f"{DOMAIN}.{request_rules._GUARD_SERVICE}",
        "data": {"pending_action": actions[0]["choose"][0]["sequence"][0]},
        "enabled": False,
    }
    assert nested[1] == actions[0]["choose"][0]["sequence"][0]
    default = guarded[0]["default"]
    assert default[0]["action"] == f"{DOMAIN}.{request_rules._GUARD_SERVICE}"
    assert default[0]["data"]["pending_action"] == {"scene": "scene.evening"}
    assert default[1] == {"scene": "scene.evening"}
    assert guarded[1] == {"variables": {"unchanged": True}}


def test_guard_native_actions_handles_non_list_container_values() -> None:
    actions = [{"repeat": {"count": 1, "sequence": None}}]

    assert request_rules._guard_native_actions(actions) == actions


def test_result_resolution_recurses_through_authored_response_values() -> None:
    value = {
        "summary": "{room}: {reading.value}",
        "details": [{"value": "{reading.items.0}"}, 3],
    }

    assert request_rules.resolve_result_values(
        value,
        {"room": "Kitchen"},
        {"reading": {"value": "warm", "items": ["21 C"]}},
    ) == {
        "summary": "Kitchen: warm",
        "details": [{"value": "21 C"}, 3],
    }


def test_native_result_sequence_rewrites_nested_result_and_slot_references() -> None:
    actions = [
        {
            "action": "test.read",
            "enabled": False,
            "data": {
                "result_alias": "reading",
                "attributes": {"template": "{reading.values.0} {room} {missing}"},
            },
        },
        {"action": "test.log", "data": {"values": [{"template": "{reading.value}"}]}},
    ]

    sequence = request_rules._native_result_sequence(actions, {"room": "Kitchen"})

    assert sequence[0]["data"]["attributes"]["template"] == (
        "{{ reading['values'][('0' if reading['values'] is mapping else 0)] }} "
        "{{ room }} {missing}"
    )
    assert sequence[0]["response_variable"] == "__eoai_result_reading"
    assert sequence[1] == {
        "variables": {"reading": "{{ __eoai_result_reading.result }}"}
    }
    assert sequence[2]["data"]["values"][0]["template"] == ("{{ reading['value'] }}")
    assert sequence[0]["enabled"] is False


@pytest.mark.parametrize(
    "rule",
    [
        {"action_type": "local_action", "action": {}},
        {"action_type": "model_routing", "action": {"reset": True}},
        {"action_type": "model_routing", "action": {"model": "{captured}"}},
    ],
)
def test_rule_model_validation_defers_non_routes_resets_and_capture_values(
    monkeypatch, rule
) -> None:
    validate = Mock(side_effect=AssertionError("no static route validation expected"))
    monkeypatch.setattr(request_rules, "validate_routed_request_options", validate)

    request_rules.validate_rule_model_request(rule, {}, {})

    validate.assert_not_called()


@pytest.mark.asyncio
async def test_action_guard_service_requires_active_authorizer(hass) -> None:
    registered = {}

    def register(domain, service, handler):
        registered[(domain, service)] = handler

    hass.services.has_service.return_value = False
    hass.services.async_register.side_effect = register

    request_rules._ensure_action_guard_service(hass)

    handler = registered[(DOMAIN, request_rules._GUARD_SERVICE)]
    with pytest.raises(HomeAssistantError, match="No active Request Rule"):
        await handler(
            SimpleNamespace(data={"pending_action": {"action": "light.turn_on"}})
        )


@pytest.mark.asyncio
async def test_action_guard_service_calls_active_authorizer_and_registers_once(
    hass,
) -> None:
    registered = {}
    hass.services.has_service.return_value = False
    hass.services.async_register.side_effect = lambda domain, service, handler: (
        registered.__setitem__((domain, service), handler)
    )
    request_rules._ensure_action_guard_service(hass)
    authorizer = Mock()
    token = request_rules._ACTIVE_ACTION_GUARD.set(authorizer)
    try:
        pending = {"action": "light.turn_on"}
        await registered[(DOMAIN, request_rules._GUARD_SERVICE)](
            SimpleNamespace(data={"pending_action": pending})
        )
    finally:
        request_rules._ACTIVE_ACTION_GUARD.reset(token)

    authorizer.assert_called_once_with(pending)

    hass.services.has_service.return_value = True
    request_rules._ensure_action_guard_service(hass)
    assert hass.services.async_register.call_count == 1


def test_guest_script_function_action_respects_configured_tool_policy(hass) -> None:
    allowed = GuestCapabilityPolicy(
        guest_active=True,
        configured_tool_names=frozenset({"safe_tool"}),
    )
    denied = GuestCapabilityPolicy(
        guest_active=True,
        configured_tool_names=frozenset(),
    )
    sequence = [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": "safe_tool", "arguments": {}},
        }
    ]

    assert request_rules._guest_script_allowed(hass, sequence, allowed)
    assert not request_rules._guest_script_allowed(hass, sequence, denied)


@pytest.mark.parametrize(
    "action",
    [
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": "bad",
        },
        {
            "action": f"{DOMAIN}.{SERVICE_CALL_FUNCTION}",
            "data": {"function": 123},
        },
        {"action": 123},
        {"device_id": "device-private"},
        {"event": "private_event"},
    ],
)
def test_guest_script_rejects_malformed_or_unbounded_effects(hass, action) -> None:
    policy = GuestCapabilityPolicy(
        guest_active=True,
        configured_tool_names=frozenset({"safe_tool"}),
    )

    assert not request_rules._guest_script_allowed(hass, [action], policy)


def test_guest_script_scene_is_checked_as_scene_turn_on(hass, monkeypatch) -> None:
    policy = GuestCapabilityPolicy(guest_active=True)
    check = Mock(return_value=True)
    monkeypatch.setattr(request_rules, "guest_arguments_allowed_runtime", check)

    assert request_rules._guest_script_allowed(
        hass,
        [{"scene": "scene.evening"}],
        policy,
    )
    pending = check.call_args.args[1]
    assert pending == {
        "action": "scene.turn_on",
        "target": {"entity_id": "scene.evening"},
    }
