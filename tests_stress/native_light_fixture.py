"""Native HA light services for stress tests that verify target authorization."""

import logging

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.helpers.entity_component import EntityComponent


async def install_lights(hass, entity_ids, handler):
    """Register real participating entities and forward their native ServiceCalls."""
    component = EntityComponent(logging.getLogger(__name__), "light", hass)
    lights = []
    for entity_id in entity_ids:
        light = LightEntity()
        light.entity_id = entity_id
        light._attr_name = entity_id
        light._attr_should_poll = False
        light._attr_is_on = True
        light._attr_supported_color_modes = frozenset({ColorMode.ONOFF})
        light._attr_color_mode = ColorMode.ONOFF
        lights.append(light)
    await component.async_add_entities(lights)
    assert [light.entity_id for light in lights] == list(entity_ids)

    async def turn_off(entity, call):
        await handler(call)
        entity._attr_is_on = False
        entity.async_write_ha_state()

    component.async_register_entity_service("turn_off", {}, turn_off)
    return component
