"""Regression coverage for direct Guest operations and trusted context boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    ha_actions,
    intercom_panel,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_GUEST_POLICY,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.functions.security import (
    contains_indirect_service_call,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
    guest_arguments_allowed_runtime,
)
from custom_components.extended_openai_conversation_responses.prompt import (
    _render_template,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRuleRuntime,
    _async_guest_functions_allowed,
    async_evaluate_rule,
)
from custom_components.extended_openai_conversation_responses.template import (
    ExtendedOpenAITemplateManager,
)
from homeassistant.core import HomeAssistant
from tests.test_request_rules import local_rule, manager


@pytest.mark.parametrize(
    "restriction",
    ["disabled", "group", "unscopable", "legacy", "result", "missing", "allowed"],
)
async def test_guest_function_preflight_checks_current_availability(hass, restriction):
    tool = {
        "enabled": restriction != "disabled",
        "spec": {
            "name": "read",
            "description": "Read",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "get_history"},
    }
    if restriction == "unscopable":
        tool["function"] = {"type": "template", "value_template": "private"}
    groups = [
        {
            "id": "reads",
            "name": "Reads",
            "description": "Reads",
            "functions": ["read"],
            "loading_mode": "always",
            "enabled": restriction != "group",
        }
    ]
    arguments = {"value": "{reading.level}"} if restriction == "result" else {}
    action = {
        "action": "extended_openai_conversation_responses.call_function",
        "data": {
            "function": "missing" if restriction == "missing" else "read",
            "arguments": arguments,
        },
    }
    policy = GuestCapabilityPolicy(True, legacy_function_flags=restriction == "legacy")
    assert await _async_guest_functions_allowed(
        hass,
        [action],
        policy,
        {},
        {"functions": [deepcopy(tool)], "function_groups": groups},
    ) is (restriction == "allowed")


@pytest.mark.parametrize("domain", ["script", "automation", "scene"])
@pytest.mark.parametrize("service", ["turn_on", "turn_off", "toggle"])
def test_generic_aliases_preserve_guest_indirect_restrictions(
    hass, monkeypatch, domain, service
):
    monkeypatch.setattr(
        ha_actions, "_resolve_target_entity_ids", lambda *_: {f"{domain}.private"}
    )
    hass.services.has_service.return_value = True
    action = {
        "domain": "homeassistant",
        "service": service,
        "target": {"area_id": "room"},
    }
    assert contains_indirect_service_call(action, hass)
    assert not guest_arguments_allowed_runtime(
        hass, action, GuestCapabilityPolicy(True), control=True
    )
    # Only the directly requested action is inspected, not a script's contents.
    assert not contains_indirect_service_call(
        {"domain": "light", "service": "turn_on"}, hass
    )


def test_area_authorization_uses_participants_and_preserves_missing_explicit_ids(
    hass, monkeypatch
):
    monkeypatch.setattr(
        ha_actions,
        "_resolve_target_entity_ids",
        lambda *_: {"light.public", "sensor.private"},
    )
    monkeypatch.setattr(native, "_service_participants", lambda *_: {"light.public"})
    action = {"action": "light.turn_on", "target": {"area_id": "room"}}
    assert ha_actions.resolve_action_entity_ids(
        hass, "light", "turn_on", target=action["target"]
    ) == {"light.public"}
    policy = GuestCapabilityPolicy(
        True, controllable_entity_ids=frozenset({"light.public"})
    )
    assert guest_arguments_allowed_runtime(
        hass, action, policy, control=True, require_entity_selector=True
    )
    hass.states.get.return_value = None
    assert "light.missing" in ha_actions.resolve_action_entity_ids(
        hass, "light", "turn_on", target={"entity_id": "light.missing"}
    )
    assert ha_actions._target_selection({"entity_id": "light.a, light.b"}, None)[
        "entity_id"
    ] == ["light.a", "light.b"]


async def test_guest_function_preflight_prevents_earlier_action(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    effects = []
    hass.services.async_register(
        "homeassistant", "update_entity", lambda call: effects.append(call)
    )
    hass.states.async_set("light.public", "on")
    hass.states.async_set("light.private", "on")
    rule = local_rule(phrases=["run"])
    rule["action"]["actions"] = [
        {
            "action": "homeassistant.update_entity",
            "target": {"entity_id": "light.public"},
        },
        {
            "action": "extended_openai_conversation_responses.call_function",
            "data": {
                "function": "control",
                "arguments": {
                    "domain": "homeassistant",
                    "service": "update_entity",
                    "entity_id": "light.private",
                },
            },
        },
    ]
    tool = {
        "enabled": True,
        "spec": {
            "name": "control",
            "description": "Control",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service_single"},
    }
    policy = GuestCapabilityPolicy(
        True, controllable_entity_ids=frozenset({"light.public"})
    )
    try:
        result = await async_evaluate_rule(
            hass,
            await manager(rule),
            RequestRuleRuntime(),
            "run",
            "guest",
            guest_policy=policy,
            request_options={"functions": [tool]},
            function_executor=AsyncMock(),
        )
        assert not result.successful
        assert effects == []
    finally:
        await hass.async_stop(force=True)


async def test_prompt_helpers_are_guest_aware_and_admin_templates_remain_trusted(
    tmp_path, monkeypatch
):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("sensor.private", "TRUSTED-SECRET")
    rows = [{"entity_id": "sensor.public"}, {"entity_id": "sensor.private"}]
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.template.get_exposed_entities",
        lambda *_: rows,
    )
    templates = ExtendedOpenAITemplateManager(hass)
    await templates.async_setup()
    policy = GuestCapabilityPolicy(
        True, readable_entity_ids=frozenset({"sensor.public"})
    )
    try:
        rendered = _render_template(
            hass,
            "{{ extended_openai.exposed_entities() | map(attribute='entity_id') | list }} / {{ states('sensor.private') }}",
            exposed_entities=[],
            current_device_id=None,
            user_input=None,
            skills=[],
            guest_policy=policy,
        )
        assert "sensor.public" in rendered and "sensor.private" not in rendered
        assert "TRUSTED-SECRET" in rendered
        assert _ACTIVE_GUEST_POLICY.get() is None
        assert templates._get_exposed_entities() == rows
    finally:
        await templates.async_on_unload()
        await hass.async_stop(force=True)


async def test_broadcast_snapshot_filters_history_targets_and_origins(
    hass, monkeypatch
):
    public, private = "assist_satellite.public", "assist_satellite.private"
    history = [
        {
            "id": "public",
            "message": "Public",
            "targets": [public],
            "deliveries": {public: {}},
            "origin_device_id": "device",
        },
        {"id": "mixed", "message": "Secret", "targets": [public, private]},
        {
            "id": "origin",
            "message": "Secret",
            "targets": [public],
            "origin_entity_id": private,
        },
    ]

    def catalog(*, entity_ids=None):
        return {
            "satellites": [
                {"id": item}
                for item in (public, private)
                if entity_ids is None or item in entity_ids
            ]
        }

    broadcast = SimpleNamespace(enabled=True, catalog=catalog, history=lambda: history)
    monkeypatch.setattr(
        intercom_panel, "async_get_intercom", AsyncMock(return_value=broadcast)
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(
            is_admin=False,
            permissions=SimpleNamespace(
                check_entity=lambda entity_id, _: entity_id == public
            ),
        ),
        send_result=Mock(),
    )
    await intercom_panel.websocket_broadcast.__wrapped__(
        hass, connection, {"id": 1, "action": "snapshot"}
    )
    result = connection.send_result.call_args.args[1]
    assert result["catalog"]["satellites"] == [{"id": public}]
    assert [item["id"] for item in result["history"]] == ["public"]
    assert result["history"][0]["origin_device_id"] is None
