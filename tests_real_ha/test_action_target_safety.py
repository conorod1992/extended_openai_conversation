"""Service participation and Broadcast ambiguity against real HA registries."""

import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.functions.script import (
    _AuthorizedScriptServices,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    action_target_revalidation,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    IntercomManager,
    parse_targeted_broadcast,
)
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_SUPPORTED_FEATURES
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
    label_registry as lr,
)
from homeassistant.helpers.entity_component import EntityComponent


class AuditLight(LightEntity):
    _attr_name = "Audit Light"
    _attr_unique_id = "audit-light"
    _attr_is_on = False
    _attr_should_poll = False
    _attr_supported_color_modes = frozenset({ColorMode.ONOFF})
    _attr_color_mode = ColorMode.ONOFF

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self.async_write_ha_state()


@pytest.mark.parametrize("selector", ["area_id", "device_id", "floor_id", "label_id"])
@pytest.mark.parametrize("cross_domain", [False, True])
async def test_indirect_target_ignores_unrelated_unavailable_sensor(
    hass, selector, cross_domain
):
    component = EntityComponent(logging.getLogger(__name__), "light", hass)
    light = AuditLight()
    await component.async_add_entities([light])
    component.async_register_entity_service("turn_on", {}, "async_turn_on")
    floor = fr.async_get(hass).async_create("Audit Floor")
    area = ar.async_get(hass).async_create("Audit Kitchen", floor_id=floor.floor_id)
    label = lr.async_get(hass).async_create("Audit Label")
    entry = MockConfigEntry(domain="audit_probe")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("audit_probe", "device")}
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id)
    registry = er.async_get(hass)
    registry.async_update_entity(
        light.entity_id, device_id=device.id, labels={label.label_id}
    )
    sensor = registry.async_get_or_create(
        "sensor", "audit_probe", "temperature", device_id=device.id
    )
    registry.async_update_entity(sensor.entity_id, labels={label.label_id})
    hass.states.async_set(sensor.entity_id, "unavailable")
    exposed = [{"entity_id": light.entity_id}, {"entity_id": sensor.entity_id}]
    target = {
        selector: {
            "area_id": area.id,
            "device_id": device.id,
            "floor_id": floor.floor_id,
            "label_id": label.label_id,
        }[selector]
    }
    arguments = {
        "list": [
            {
                "domain": "homeassistant" if cross_domain else "light",
                "service": "turn_on",
                "service_data": target,
            }
        ]
    }
    # Exercise the shared dispatch-boundary exposure check as conversation does.
    # The sensor is deliberately unexposed, despite sharing every selector.
    exposed = [{"entity_id": light.entity_id}]

    def revalidate(current_hass, entity_ids):
        assert entity_ids == {light.entity_id}
        NativeFunction().validate_entity_ids(current_hass, sorted(entity_ids), exposed)

    with action_target_revalidation(revalidate):
        result = await NativeFunction().execute(
            hass, {"name": "execute_service"}, arguments, None, exposed
        )
    assert result[0]["success"] is True
    assert hass.states.get(light.entity_id).state == "on"
    # Intended targets must still pass availability and exposure checks.
    hass.states.async_set(light.entity_id, "unavailable")
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await NativeFunction().execute(
            hass, {"name": "execute_service"}, arguments, None, exposed
        )
    hass.states.async_set(light.entity_id, "off")
    # Unrelated sensors do not participate in the light service.
    await NativeFunction().execute(
        hass, {"name": "execute_service"}, arguments, None,
        [{"entity_id": light.entity_id}],
    )
    with pytest.raises(HomeAssistantError):
        await NativeFunction().execute(
            hass,
            {"name": "execute_service"},
            arguments,
            None,
            [{"entity_id": sensor.entity_id}],
        )


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
async def test_broadcast_duplicate_names_are_ambiguous(hass, alias, reverse):
    registry = er.async_get(hass)
    names = ["kitchen", "bedroom"]
    for name in reversed(names) if reverse else names:
        entity = registry.async_get_or_create(
            "assist_satellite", "audit_probe", name, suggested_object_id=name
        )
        registry.async_update_entity(
            entity.entity_id, aliases={"Voice"} if alias else set()
        )
        hass.states.async_set(
            entity.entity_id,
            "responding",
            {
                ATTR_FRIENDLY_NAME: name if alias else "Voice",
                ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE,
            },
        )
    manager = IntercomManager(hass)
    with pytest.raises(HomeAssistantError, match="Ambiguous Broadcast destination"):
        manager.resolve_named_target("Voice")
    with pytest.raises(HomeAssistantError, match="Ambiguous Broadcast destination"):
        parse_targeted_broadcast("broadcast to Voice dinner is ready", manager)
    assert manager.history() == []
    assert not manager._queues


async def test_satellite_and_device_same_destination_are_not_ambiguous(hass):
    entry = MockConfigEntry(domain="audit_probe")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("audit_probe", "voice")},
        name="Voice",
    )
    entity = er.async_get(hass).async_get_or_create(
        "assist_satellite", "audit_probe", "voice", device_id=device.id
    )
    hass.states.async_set(
        entity.entity_id,
        "responding",
        {
            ATTR_FRIENDLY_NAME: "Voice",
            ATTR_SUPPORTED_FEATURES: ANNOUNCE_FEATURE,
        },
    )
    manager = IntercomManager(hass)
    assert manager.resolve_named_target("Voice")["entity_ids"] == [entity.entity_id]
    assert parse_targeted_broadcast("broadcast to Voice dinner is ready", manager) == (
        {"entity_ids": [entity.entity_id]},
        "dinner is ready",
    )


@pytest.mark.parametrize("target_field", ["service_data", "target"])
async def test_script_all_target_cannot_skip_exposure_authorization(hass, target_field):
    component = EntityComponent(logging.getLogger(__name__), "light", hass)
    public = AuditLight()
    public._attr_unique_id = "public-light"
    private = AuditLight()
    private._attr_unique_id = "private-light"
    await component.async_add_entities([public, private])
    component.async_register_entity_service("turn_on", {}, "async_turn_on")
    services = _AuthorizedScriptServices(
        hass, NativeFunction(), [{"entity_id": public.entity_id}]
    )
    with pytest.raises(HomeAssistantError, match="entity_id 'all' is not supported"):
        await services.async_call(
            "light", "turn_on", blocking=True, **{target_field: {"entity_id": "all"}}
        )
    assert hass.states.get(private.entity_id).state == "off"
    assert hass.states.get(public.entity_id).state == "off"


@pytest.mark.parametrize("domain_name", ["light", "homeassistant"])
@pytest.mark.parametrize("spelling", ["lower", "domain", "service", "both"])
@pytest.mark.parametrize("selector", ["area_id", "entity_id"])
@pytest.mark.parametrize("route", ["single", "batch", "shared"])
async def test_service_casing_preserves_participants_and_previous_state(
    hass, monkeypatch, domain_name, spelling, selector, route
):
    """HA-equivalent names keep policy targets, state and dispatch identical."""
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses.built_in_functions import (
        BUILT_IN_FUNCTION_PRESETS,
    )
    from custom_components.extended_openai_conversation_responses.function_execution import (
        validate_function_arguments,
    )
    from custom_components.extended_openai_conversation_responses.ha_actions import (
        async_call_ha_action,
    )

    component = EntityComponent(logging.getLogger(__name__), "light", hass)
    light = AuditLight()
    effects = []
    turn_on = light.async_turn_on

    async def record_turn_on(**kwargs):
        effects.append(dict(kwargs))
        await turn_on(**kwargs)

    monkeypatch.setattr(light, "async_turn_on", record_turn_on)
    await component.async_add_entities([light])
    component.async_register_entity_service("turn_on", {}, "async_turn_on")
    area = ar.async_get(hass).async_create("Casing Kitchen")
    registry = er.async_get(hass)
    registry.async_update_entity(light.entity_id, area_id=area.id)
    sensor = registry.async_get_or_create("sensor", "audit_probe", "casing_temperature")
    registry.async_update_entity(sensor.entity_id, area_id=area.id)
    hass.states.async_set(sensor.entity_id, "20")
    exposed = [{"entity_id": light.entity_id}]
    domain = domain_name.upper() if spelling in {"domain", "both"} else domain_name
    service = "TURN_ON" if spelling in {"service", "both"} else "turn_on"
    data = {selector: area.id if selector == "area_id" else light.entity_id}
    arguments = {"domain": domain, "service": service, "service_data": data}
    original = deepcopy(arguments)
    seen = []

    def revalidate(current_hass, entity_ids):
        assert entity_ids == {light.entity_id}
        NativeFunction().validate_entity_ids(current_hass, sorted(entity_ids), exposed)
        seen.append(entity_ids)

    with action_target_revalidation(revalidate):
        if route == "shared":
            result = await async_call_ha_action(
                hass, domain, service, data=data, blocking=True, include_response=True
            )
        else:
            implementation = (
                "execute_service_single" if route == "single" else "execute_service"
            )
            tool = next(
                item["tool"]
                for item in BUILT_IN_FUNCTION_PRESETS
                if item["tool"]["function"]["name"] == implementation
            )
            supplied = arguments if route == "single" else {"list": [arguments]}
            validate_function_arguments(tool["spec"], supplied)
            result = await NativeFunction().execute(
                hass, tool["function"], supplied, None, exposed
            )
            if route == "batch":
                result = result[0]
            assert result["success"] is True
    assert seen == [{light.entity_id}]
    assert result["previous_state"] == {light.entity_id: {"state": "off"}}
    assert hass.states.get(light.entity_id).state == "on"
    assert hass.states.get(sensor.entity_id).state == "20"
    assert arguments == original

    # Casing must never hide a genuine exposure denial or dispatch another effect.
    assert len(effects) == 1
    exposed.clear()
    with action_target_revalidation(revalidate), pytest.raises(HomeAssistantError):
        if route == "shared":
            await async_call_ha_action(hass, domain, service, data=data, blocking=True)
        else:
            await NativeFunction().execute(
                hass, tool["function"], supplied, None, exposed
            )
    assert len(effects) == 1
