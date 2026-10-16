"""Indirect service participants agree with native HA availability filtering."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from homeassistant.core import Context, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import service

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)


@pytest.fixture
def selected_lights(hass, monkeypatch):
    entities = {
        name: SimpleNamespace(entity_id=name, available=available)
        for name, available in [("light.online", True), ("light.offline", False)]
    }

    def select(_hass, selection, *args, **kwargs):
        return SimpleNamespace(
            referenced=set(selection.entity_ids),
            indirectly_referenced=set(entities) if selection.has_any_target else set(),
            log_missing=Mock(),
        )

    monkeypatch.setattr(
        ha_actions.target_helpers, "async_extract_referenced_entity_ids", select
    )
    registered = SimpleNamespace(
        job=SimpleNamespace(target=partial(service.entity_service_call, hass, entities))
    )
    hass.services.async_services_for_domain.side_effect = lambda _domain: {
        "turn_off": registered
    }
    hass.services.has_service.return_value = True
    hass.states.get.side_effect = lambda entity_id: State(
        entity_id, "on" if entities[entity_id].available else "unavailable"
    )
    return entities


@pytest.mark.parametrize("selector", ["area_id", "device_id", "floor_id", "label_id"])
@pytest.mark.parametrize("domain", ["light", "homeassistant"])
async def test_available_sibling_remains_controllable(
    hass, selected_lights, selector, domain
):
    data = {selector: "kitchen"}
    # Exercise HA's actual dispatcher resolver, instrumenting only registry selection.
    native = await service._resolve_entity_service_call_entities(
        hass,
        selected_lights,
        ServiceCall(hass, "light", "turn_off", data, context=Context()),
    )
    expected = {entity.entity_id for entity in native}
    assert expected == {"light.online"}
    assert (
        ha_actions.resolve_action_entity_ids(hass, domain, "turn_off", data) == expected
    )
    # The offline entity is deliberately unexposed: it will not participate.
    result = await NativeFunction().execute_service_single(
        hass,
        {},
        {"domain": domain, "service": "turn_off", "service_data": data},
        None,
        [{"entity_id": "light.online"}],
    )
    assert result["success"]
    hass.services.async_call.assert_awaited_once()


@pytest.mark.parametrize(
    "selected", ["light.offline", ["light.online", "light.offline"]]
)
def test_explicit_offline_targets_still_error(hass, selected_lights, selected):
    with pytest.raises(HomeAssistantError, match="unavailable"):
        NativeFunction().validate_service_targets(
            hass,
            {"entity_id": selected},
            [{"entity_id": e} for e in selected_lights],
            domain="light",
            service="turn_off",
        )


def test_available_participants_still_require_exposure(hass, selected_lights):
    with pytest.raises(HomeAssistantError):
        NativeFunction().validate_service_targets(
            hass, {"area_id": "kitchen"}, [], domain="light", service="turn_off"
        )


def test_recovery_and_unknown_services_keep_offline_targets(hass, selected_lights):
    exposed = [{"entity_id": e} for e in selected_lights]
    NativeFunction().validate_service_targets(
        hass,
        {"area_id": "kitchen"},
        exposed,
        domain="homeassistant",
        service="update_entity",
    )
    hass.services.async_services_for_domain.return_value = {}
    hass.services.async_services_for_domain.side_effect = None
    assert ha_actions.resolve_action_entity_ids(
        hass, "custom", "turn_off", {"area_id": "kitchen"}
    ) == set(selected_lights)
    with pytest.raises(HomeAssistantError, match="unavailable"):
        NativeFunction().validate_service_targets(
            hass, {"area_id": "kitchen"}, exposed, domain="custom", service="turn_off"
        )


@pytest.mark.parametrize("legacy", [False, True])
def test_availability_uses_live_entities_for_callable_and_platform_registrations(
    hass, selected_lights, legacy
):
    mapping = (
        [SimpleNamespace(entities=selected_lights)]
        if legacy
        else lambda: selected_lights
    )
    registered = SimpleNamespace(
        job=SimpleNamespace(target=partial(service.entity_service_call, hass, mapping))
    )
    hass.services.async_services_for_domain.side_effect = lambda _: {
        "turn_off": registered
    }
    assert ha_actions.resolve_action_entity_ids(
        hass, "light", "turn_off", {"area_id": "kitchen"}
    ) == {"light.online"}
