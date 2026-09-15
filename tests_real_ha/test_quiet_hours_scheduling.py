"""Real Home Assistant scheduling coverage for global Quiet Hours."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

_STATE_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"
_DUBLIN = ZoneInfo("Europe/Dublin")


def _install_satellite_entities(
    hass: HomeAssistant,
    *,
    slug: str = "bedroom",
    name: str = "Bedroom Voice",
    volume: float = 0.60,
    wake: str = "on",
) -> tuple[str, str, str]:
    """Create one real registry-backed Assist satellite device and its controls."""
    source_entry = MockConfigEntry(domain="esphome", title=f"{name} test device")
    source_entry.add_to_hass(hass)

    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source_entry.entry_id,
        identifiers={("esphome", f"quiet-hours-{slug}-scheduling")},
        manufacturer="Home Assistant",
        model="Voice Preview Edition",
        name=name,
    )
    registry = er.async_get(hass)
    satellite = registry.async_get_or_create(
        "assist_satellite",
        "esphome",
        f"quiet-hours-{slug}-scheduling-satellite",
        config_entry=source_entry,
        suggested_object_id=f"{slug}_voice_scheduling",
        device_id=device.id,
        original_name="Assist satellite",
    )
    media_player = registry.async_get_or_create(
        "media_player",
        "esphome",
        f"quiet-hours-{slug}-scheduling-media-player",
        config_entry=source_entry,
        suggested_object_id=f"{slug}_voice_scheduling",
        device_id=device.id,
        original_name="Media Player",
    )
    wake_sound = registry.async_get_or_create(
        "switch",
        "esphome",
        f"quiet-hours-{slug}-scheduling-wake-sound",
        config_entry=source_entry,
        suggested_object_id=f"{slug}_voice_scheduling_wake_sound",
        device_id=device.id,
        original_name="Wake sound",
    )

    hass.states.async_set(
        satellite.entity_id,
        "idle",
        {"friendly_name": name},
    )
    hass.states.async_set(
        media_player.entity_id,
        "idle",
        {"friendly_name": name, "volume_level": volume},
    )
    hass.states.async_set(
        wake_sound.entity_id,
        wake,
        {"friendly_name": f"{name} Wake sound"},
    )
    return satellite.entity_id, media_player.entity_id, wake_sound.entity_id


def _install_control_services(hass: HomeAssistant) -> None:
    """Register control services that update the real HA state machine."""

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
async def test_real_ha_clock_callbacks_activate_and_restore(
    hass: HomeAssistant,
) -> None:
    """Prove HA's local wall-clock callbacks drive start and end transitions."""
    _satellite_id, media_player_id, wake_sound_id = _install_satellite_entities(hass)
    _install_control_services(hass)

    now = dt_util.now()
    start_at = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    end_at = (now + timedelta(minutes=6)).replace(second=0, microsecond=0)

    manager = await async_get_quiet_hours(hass)
    try:
        await manager.async_update_config(
            {
                "enabled": True,
                "start": start_at.strftime("%H:%M"),
                "end": end_at.strftime("%H:%M"),
                "max_volume": 0.20,
                "wake_sound": "off",
                "overrides": {},
            }
        )
        await hass.async_block_till_done()

        media_state = hass.states.get(media_player_id)
        wake_state = hass.states.get(wake_sound_id)
        quiet_state = hass.states.get(_STATE_ENTITY_ID)
        assert media_state is not None
        assert wake_state is not None
        assert quiet_state is not None
        assert media_state.attributes["volume_level"] == pytest.approx(0.60)
        assert wake_state.state == "on"
        assert quiet_state.state == "off"
        assert len(manager._unsubscribers) == 3

        async_fire_time_changed(hass, start_at.astimezone(UTC))
        await hass.async_block_till_done()

        media_state = hass.states.get(media_player_id)
        wake_state = hass.states.get(wake_sound_id)
        quiet_state = hass.states.get(_STATE_ENTITY_ID)
        assert media_state is not None
        assert wake_state is not None
        assert quiet_state is not None
        assert media_state.attributes["volume_level"] == pytest.approx(0.20)
        assert wake_state.state == "off"
        assert quiet_state.state == "on"

        async_fire_time_changed(hass, end_at.astimezone(UTC))
        await hass.async_block_till_done()

        media_state = hass.states.get(media_player_id)
        wake_state = hass.states.get(wake_sound_id)
        quiet_state = hass.states.get(_STATE_ENTITY_ID)
        assert media_state is not None
        assert wake_state is not None
        assert quiet_state is not None
        assert media_state.attributes["volume_level"] == pytest.approx(0.60)
        assert wake_state.state == "on"
        assert quiet_state.state == "off"
        assert manager.active is None
    finally:
        await manager.async_shutdown()


@pytest.mark.asyncio
async def test_real_ha_discovery_tick_normalizes_utc_to_ha_local_time(
    hass: HomeAssistant,
) -> None:
    """A UTC interval callback must reconcile against the HA-local schedule."""
    await hass.config.async_set_time_zone("Europe/Dublin")
    _satellite_id, media_player_id, wake_sound_id = _install_satellite_entities(hass)
    _install_control_services(hass)

    manager = await async_get_quiet_hours(hass)
    try:
        await manager.async_update_config(
            {
                "enabled": True,
                "start": "22:00",
                "end": "07:00",
                "max_volume": 0.20,
                "wake_sound": "off",
                "overrides": {},
            }
        )

        await manager.async_reconcile(
            now=datetime(2026, 7, 15, 22, 0, tzinfo=_DUBLIN)
        )
        media_state = hass.states.get(media_player_id)
        wake_state = hass.states.get(wake_sound_id)
        assert media_state is not None
        assert wake_state is not None
        assert media_state.attributes["volume_level"] == pytest.approx(0.20)
        assert wake_state.state == "off"

        _kitchen_satellite_id, kitchen_media_id, kitchen_wake_id = (
            _install_satellite_entities(
                hass,
                slug="kitchen",
                name="Kitchen Voice",
                volume=0.75,
                wake="on",
            )
        )
        kitchen_media = hass.states.get(kitchen_media_id)
        kitchen_wake = hass.states.get(kitchen_wake_id)
        assert kitchen_media is not None
        assert kitchen_wake is not None
        assert kitchen_media.attributes["volume_level"] == pytest.approx(0.75)
        assert kitchen_wake.state == "on"

        # async_track_time_interval supplies UTC. 21:05 UTC is 22:05 in Dublin.
        await manager._handle_discovery_tick(
            datetime(2026, 7, 15, 21, 5, tzinfo=UTC)
        )

        media_state = hass.states.get(media_player_id)
        wake_state = hass.states.get(wake_sound_id)
        kitchen_media = hass.states.get(kitchen_media_id)
        kitchen_wake = hass.states.get(kitchen_wake_id)
        quiet_state = hass.states.get(_STATE_ENTITY_ID)
        assert media_state is not None
        assert wake_state is not None
        assert kitchen_media is not None
        assert kitchen_wake is not None
        assert quiet_state is not None
        assert media_state.attributes["volume_level"] == pytest.approx(0.20)
        assert wake_state.state == "off"
        assert kitchen_media.attributes["volume_level"] == pytest.approx(0.20)
        assert kitchen_wake.state == "off"
        assert quiet_state.state == "on"
        assert manager.active is not None
    finally:
        await manager.async_shutdown()
