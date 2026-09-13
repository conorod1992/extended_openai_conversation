"""Real-Home-Assistant acceptance coverage for global Quiet Hours."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

_STATE_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"
_RUNTIME_KEY = "quiet_hours_manager"
_SATELLITE = "assist_satellite.bedroom_voice"
_MEDIA_PLAYER = "media_player.bedroom_voice"
_WAKE_SOUND = "switch.bedroom_voice_wake_sound"


def _active_window() -> tuple[str, str]:
    """Return a schedule that safely contains the current HA-local time."""
    now = dt_util.now()
    return (
        (now - timedelta(hours=1)).strftime("%H:%M"),
        (now + timedelta(hours=1)).strftime("%H:%M"),
    )


def _install_satellite_entities(hass: HomeAssistant) -> None:
    """Create one real registry-backed Assist satellite device and its controls."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id="quiet-hours-acceptance",
        identifiers={(DOMAIN, "quiet-hours-bedroom-voice")},
        manufacturer="Home Assistant",
        model="Voice Preview Edition",
        name="Bedroom Voice",
    )
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "assist_satellite",
        "esphome",
        "quiet-hours-bedroom-satellite",
        suggested_object_id="bedroom_voice",
        device_id=device.id,
        original_name="Assist satellite",
    )
    registry.async_get_or_create(
        "media_player",
        "esphome",
        "quiet-hours-bedroom-media-player",
        suggested_object_id="bedroom_voice",
        device_id=device.id,
        original_name="Media Player",
    )
    registry.async_get_or_create(
        "switch",
        "esphome",
        "quiet-hours-bedroom-wake-sound",
        suggested_object_id="bedroom_voice_wake_sound",
        device_id=device.id,
        original_name="Wake sound",
    )

    hass.states.async_set(
        _SATELLITE,
        "idle",
        {"friendly_name": "Bedroom Voice"},
    )
    hass.states.async_set(
        _MEDIA_PLAYER,
        "idle",
        {"friendly_name": "Bedroom Voice", "volume_level": 0.60},
    )
    hass.states.async_set(
        _WAKE_SOUND,
        "on",
        {"friendly_name": "Bedroom Voice Wake sound"},
    )


def _install_control_services(hass: HomeAssistant) -> None:
    """Register real HA services whose handlers update the real state machine."""

    async def volume_set(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        state = hass.states.get(entity_id)
        assert state is not None
        attributes = dict(state.attributes)
        attributes["volume_level"] = call.data["volume_level"]
        hass.states.async_set(entity_id, state.state, attributes)

    async def turn_on(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        state = hass.states.get(entity_id)
        assert state is not None
        hass.states.async_set(entity_id, "on", dict(state.attributes))

    async def turn_off(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        state = hass.states.get(entity_id)
        assert state is not None
        hass.states.async_set(entity_id, "off", dict(state.attributes))

    hass.services.async_register("media_player", "volume_set", volume_set)
    hass.services.async_register("switch", "turn_on", turn_on)
    hass.services.async_register("switch", "turn_off", turn_off)


@pytest.mark.asyncio
async def test_real_ha_quiet_hours_discovers_applies_survives_restart_and_restores(
    hass: HomeAssistant,
) -> None:
    """Prove the complete Quiet Hours contract across real HA-owned seams."""
    _install_satellite_entities(hass)
    _install_control_services(hass)
    start, end = _active_window()

    manager = await async_get_quiet_hours(hass)
    snapshot = await manager.async_update_config(
        {
            "enabled": True,
            "start": start,
            "end": end,
            "max_volume": 0.20,
            "wake_sound": "off",
            "overrides": {},
        }
    )
    await hass.async_block_till_done()

    # Discovery must use real entity/device registry relationships rather than a
    # hand-provided mapping.
    assert len(snapshot["satellites"]) == 1
    satellite = snapshot["satellites"][0]
    assert satellite["satellite_entity_id"] == _SATELLITE
    assert satellite["media_player_entity_id"] == _MEDIA_PLAYER
    assert satellite["media_player_source"] == "auto"
    assert satellite["wake_sound_entity_id"] == _WAKE_SOUND
    assert satellite["wake_sound_source"] == "auto"

    # The real HA service registry/state machine must reflect the applied policy.
    assert hass.states[_MEDIA_PLAYER].attributes["volume_level"] == pytest.approx(0.20)
    assert hass.states[_WAKE_SOUND].state == "off"
    quiet_state = hass.states.get(_STATE_ENTITY_ID)
    assert quiet_state is not None
    assert quiet_state.state == "on"
    assert quiet_state.attributes["max_volume"] == pytest.approx(0.20)

    active = manager.active
    assert active is not None
    assert active["controls"][_MEDIA_PLAYER]["original_value"] == pytest.approx(0.60)
    assert active["controls"][_WAKE_SOUND]["original_value"] is True
    first_period_id = active["period_started_at"]

    # Recreate the domain-global manager against the same HA storage to model an
    # integration/HA restart.  The already-ducked value must not become the new
    # baseline, and the same schedule occurrence must retain its ownership record.
    await manager.async_shutdown()
    hass.data[DOMAIN].pop(_RUNTIME_KEY, None)
    restarted = await async_get_quiet_hours(hass)
    await hass.async_block_till_done()

    restarted_active = restarted.active
    assert restarted_active is not None
    assert restarted_active["period_started_at"] == first_period_id
    assert restarted_active["controls"][_MEDIA_PLAYER]["original_value"] == pytest.approx(
        0.60
    )
    assert restarted_active["controls"][_WAKE_SOUND]["original_value"] is True
    assert hass.states[_MEDIA_PLAYER].attributes["volume_level"] == pytest.approx(0.20)
    assert hass.states[_WAKE_SOUND].state == "off"
    assert hass.states[_STATE_ENTITY_ID].state == "on"

    # Disabling the schedule uses the same conditional restoration path as the end
    # boundary and must return both controls to their pre-Quiet-Hours values.
    disabled = restarted.config.as_dict()
    disabled["enabled"] = False
    await restarted.async_update_config(disabled)
    await hass.async_block_till_done()

    assert hass.states[_MEDIA_PLAYER].attributes["volume_level"] == pytest.approx(0.60)
    assert hass.states[_WAKE_SOUND].state == "on"
    assert restarted.active is None
    final_state = hass.states.get(_STATE_ENTITY_ID)
    assert final_state is not None
    assert final_state.state == "off"
    assert final_state.attributes["enabled"] is False
