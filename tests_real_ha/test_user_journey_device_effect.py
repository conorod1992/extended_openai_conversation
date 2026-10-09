"""User-journey tests: real HA entities and service-side effects."""
from __future__ import annotations

import logging

import pytest
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import EntityComponent

from tests_real_ha.test_provider_wire_e2e import (
    _agent, _chat_sse_text, _chat_sse_tool_call, _install_wire, _say, _speech,
)
from custom_components.extended_openai_conversation_responses.const import API_MODE_CHAT_COMPLETIONS


class DimmableProbe(LightEntity):
    _attr_name = "Journey Dimmable Light"
    _attr_unique_id = "journey-dimmable-001"
    _attr_should_poll = False
    _attr_is_on = False
    _attr_supported_color_modes = frozenset({ColorMode.BRIGHTNESS})
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_brightness = 0

    async def async_turn_on(self, **kwargs):
        self._attr_is_on = True
        self._attr_brightness = kwargs.get("brightness", self._attr_brightness)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._attr_is_on = False
        self.async_write_ha_state()


@pytest.mark.parametrize("level", [1, 128, 255])
async def test_provider_action_updates_real_brightness_entity(
    hass: HomeAssistant, monkeypatch, level
):
    """Actual light service schema and entity handler, not a mocked service call."""
    component = EntityComponent(logging.getLogger(__name__), "light", hass)
    entity = DimmableProbe()
    await component.async_add_entities([entity])
    component.async_register_entity_service("turn_on", {"brightness": int}, "async_turn_on")
    assert entity.entity_id
    async_expose_entity(hass, conversation.DOMAIN, entity.entity_id, True)
    agent = await _agent(hass, API_MODE_CHAT_COMPLETIONS)
    _install_wire(
        monkeypatch, agent,
        [
            _chat_sse_tool_call(
                f"call-brightness-{level}", "execute_services",
                {"list": [{"domain": "light", "service": "turn_on",
                           "service_data": {"entity_id": [entity.entity_id], "brightness": level}}]},
            ),
            _chat_sse_text("Brightness changed."),
        ],
    )
    assert _speech(await _say(hass, agent)) == "Brightness changed."
    result = hass.states.get(entity.entity_id)
    assert result is not None
    assert result.state == "on"
    assert result.attributes["brightness"] == level
