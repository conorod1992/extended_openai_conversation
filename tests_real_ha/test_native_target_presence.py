"""Native target guards must agree with real HA dispatch and entity effects."""

import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.ha_actions import (
    async_call_ha_action,
)
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry,
    device_registry,
    entity_registry,
    floor_registry,
    label_registry,
)
from homeassistant.helpers.entity_component import EntityComponent


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


class _TargetLight(LightEntity):
    _attr_should_poll = False
    _attr_is_on = False
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = frozenset({ColorMode.ONOFF})

    def __init__(self, name):
        self._attr_name = f"Native target {name}"
        self._attr_unique_id = f"native-target-{name}"
        self.turn_on_calls = 0

    async def async_turn_on(self, **kwargs):
        self.turn_on_calls += 1
        self._attr_is_on = True
        self.async_write_ha_state()


@pytest.fixture
async def entity_target_home(hass):
    """Two registered lights use HA's actual entity-service target extraction."""
    component = EntityComponent(logging.getLogger(__name__), "light", hass)
    lights = [_TargetLight(name) for name in ("Kitchen", "Hall")]
    await component.async_add_entities(lights)
    component.async_register_entity_service("turn_on", {}, "async_turn_on")
    entry = MockConfigEntry(domain="native_target_probe")
    entry.add_to_hass(hass)
    targets = {key: [] for key in ("area_id", "device_id", "floor_id", "label_id")}
    for index, light in enumerate(lights):
        floor = floor_registry.async_get(hass).async_create(f"Native floor {index}")
        area = area_registry.async_get(hass).async_create(
            f"Native area {index}", floor_id=floor.floor_id
        )
        label = label_registry.async_get(hass).async_create(f"Native label {index}")
        device = device_registry.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("native_target_probe", str(index))},
        )
        device_registry.async_get(hass).async_update_device(device.id, area_id=area.id)
        entity_registry.async_get(hass).async_update_entity(
            light.entity_id, device_id=device.id, labels={label.label_id}
        )
        for key, value in {
            "area_id": area.id,
            "device_id": device.id,
            "floor_id": floor.floor_id,
            "label_id": label.label_id,
        }.items():
            targets[key].append(value)
    await hass.async_block_till_done()
    assert [hass.states.get(light.entity_id).state for light in lights] == [
        "off",
        "off",
    ]
    return lights, targets


@pytest.mark.parametrize("key", ["area_id", "device_id", "floor_id", "label_id"])
@pytest.mark.parametrize(
    "shape", ["padded_scalar", "padded_list", "comma_scalar", "comma_list"]
)
@pytest.mark.parametrize("batch", [False, True])
async def test_indirect_normalization_cannot_invent_an_entity_service_target(
    hass, entity_target_home, key, shape, batch
):
    lights, targets = entity_target_home
    first, second = targets[key]
    selector = {
        "padded_scalar": f" {first} ",
        "padded_list": [f" {first} "],
        "comma_scalar": f"{first},{second}",
        "comma_list": [f"{first},{second}"],
    }[shape]
    arguments = {
        "domain": "light",
        "service": "turn_on",
        "service_data": {key: selector},
    }
    dispatched = []

    def record(event):
        if (
            event.data.get("domain") == "light"
            and event.data.get("service") == "turn_on"
        ):
            dispatched.append(event.data)

    unsubscribe = hass.bus.async_listen(EVENT_CALL_SERVICE, record)
    native = NativeFunction()
    try:
        with pytest.raises(HomeAssistantError, match="does not resolve"):
            if batch:
                await native.execute_service(
                    hass,
                    {},
                    {"list": [arguments]},
                    None,
                    [{"entity_id": light.entity_id} for light in lights],
                )
            else:
                await native.execute_service_single(
                    hass,
                    {},
                    arguments,
                    None,
                    [{"entity_id": light.entity_id} for light in lights],
                )
        await hass.async_block_till_done()
        assert dispatched == []
        assert [light.turn_on_calls for light in lights] == [0, 0]
        assert [hass.states.get(light.entity_id).state for light in lights] == [
            "off",
            "off",
        ]
    finally:
        unsubscribe()


@pytest.mark.parametrize("key", ["area_id", "device_id", "floor_id", "label_id"])
@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("empty", [[], "", " , "])
async def test_canonical_entity_service_targets_act_with_empty_alternatives(
    hass, entity_target_home, key, multiple, empty
):
    lights, targets = entity_target_home
    data = {
        name: empty
        for name in ("entity_id", "area_id", "device_id", "floor_id", "label_id")
    }
    data[key] = targets[key] if multiple else targets[key][0]
    result = await NativeFunction().execute_service_single(
        hass,
        {},
        {"domain": "light", "service": "turn_on", "service_data": data},
        None,
        [{"entity_id": light.entity_id} for light in lights],
    )
    assert result["success"]
    assert [light.turn_on_calls for light in lights] == ([1, 1] if multiple else [1, 0])
    assert [hass.states.get(light.entity_id).state for light in lights] == (
        ["on", "on"] if multiple else ["on", "off"]
    )
