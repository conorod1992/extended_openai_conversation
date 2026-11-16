"""A harmless real HA handler must never receive native empty-only targets."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_call_ha_action,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    floor_registry,
    label_registry,
)


@pytest.mark.parametrize(
    "key", ["entity_id", "area_id", "device_id", "floor_id", "label_id"]
)
async def test_real_handler_target_presence_and_indirect_positive_controls(hass, key):
    calls = []

    async def probe(call):
        calls.append(dict(call.data))

    hass.services.async_register("native_probe", "record", probe)
    native = NativeFunction()
    arguments = {"domain": "native_probe", "service": "record"}
    for empty in ([], "", " , ", ["", " , "]):
        with pytest.raises(HomeAssistantError):
            await native.execute_service_single(
                hass, {}, {**arguments, "service_data": {key: empty}}, None, []
            )
    assert calls == []

    # Trusted HA actions intentionally retain targetless handler support.
    await async_call_ha_action(hass, "native_probe", "record", blocking=True)
    assert len(calls) == 1

    floor = floor_registry.async_get(hass).async_create("Probe floor")
    label = label_registry.async_get(hass).async_create("Probe label")
    area = area_registry.async_get(hass).async_create("Probe area")
    area_registry.async_get(hass).async_update(area.id, floor_id=floor.floor_id)
    entry = MockConfigEntry(domain="extended_openai_conversation_responses")
    entry.add_to_hass(hass)
    device = device_registry.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("native_probe", "device")}
    )
    device_registry.async_get(hass).async_update_device(device.id, area_id=area.id)
    entity = entity_registry.async_get(hass).async_get_or_create(
        "sensor", "native_probe", "target", device_id=device.id
    )
    entity_registry.async_get(hass).async_update_entity(
        entity.entity_id, labels={label.label_id}
    )
    hass.states.async_set(entity.entity_id, "ready")
    targets = {
        "entity_id": entity.entity_id,
        "area_id": area.id,
        "device_id": device.id,
        "floor_id": floor.floor_id,
        "label_id": label.label_id,
    }
    data = {name: [] for name in targets}
    data[key] = targets[key]
    result = await native.execute_service_single(
        hass,
        {},
        {**arguments, "service_data": data},
        None,
        [{"entity_id": entity.entity_id}],
    )
    assert result["success"]
    assert len(calls) == 2
    assert calls[-1][key] == ([targets[key]] if key == "entity_id" else targets[key])
