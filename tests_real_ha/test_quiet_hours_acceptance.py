"""Real-Home-Assistant acceptance coverage for global Quiet Hours."""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    async_get_quiet_hours,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

_STATE_ENTITY_ID = "binary_sensor.extended_openai_quiet_hours"
_RUNTIME_KEY = "quiet_hours_manager"


def _active_window() -> tuple[str, str]:
    """Return a schedule that safely contains the current HA-local time."""
    now = dt_util.now()
    return (
        (now - timedelta(hours=1)).strftime("%H:%M"),
        (now + timedelta(hours=1)).strftime("%H:%M"),
    )


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
        identifiers={("esphome", f"quiet-hours-{slug}-voice")},
        manufacturer="Home Assistant",
        model="Voice Preview Edition",
        name=name,
    )
    registry = er.async_get(hass)
    satellite = registry.async_get_or_create(
        "assist_satellite",
        "esphome",
        f"quiet-hours-{slug}-satellite",
        config_entry=source_entry,
        suggested_object_id=f"{slug}_voice",
        device_id=device.id,
        original_name="Assist satellite",
    )
    media_player = registry.async_get_or_create(
        "media_player",
        "esphome",
        f"quiet-hours-{slug}-media-player",
        config_entry=source_entry,
        suggested_object_id=f"{slug}_voice",
        device_id=device.id,
        original_name="Media Player",
    )
    wake_sound = registry.async_get_or_create(
        "switch",
        "esphome",
        f"quiet-hours-{slug}-wake-sound",
        config_entry=source_entry,
        suggested_object_id=f"{slug}_voice_wake_sound",
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
    satellite_id, media_player_id, wake_sound_id = _install_satellite_entities(hass)
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
    assert satellite["satellite_entity_id"] == satellite_id
    assert satellite["media_player_entity_id"] == media_player_id
    assert satellite["media_player_source"] == "auto"
    assert satellite["wake_sound_entity_id"] == wake_sound_id
    assert satellite["wake_sound_source"] == "auto"

    # The real HA service registry/state machine must reflect the applied policy.
    media_state = hass.states.get(media_player_id)
    wake_state = hass.states.get(wake_sound_id)
    assert media_state is not None
    assert wake_state is not None
    assert media_state.attributes["volume_level"] == pytest.approx(0.20)
    assert wake_state.state == "off"

    quiet_state = hass.states.get(_STATE_ENTITY_ID)
    assert quiet_state is not None
    assert quiet_state.state == "on"
    assert quiet_state.attributes["max_volume"] == pytest.approx(0.20)

    active = manager.active
    assert active is not None
    assert active["controls"][media_player_id]["original_value"] == pytest.approx(0.60)
    assert active["controls"][wake_sound_id]["original_value"] is True
    first_period_id = active["period_started_at"]

    # Recreate the domain-global manager against the same HA storage to model an
    # integration/HA restart. The already-ducked value must not become the new
    # baseline, and the same schedule occurrence must retain its ownership record.
    await manager.async_shutdown()
    hass.data[DOMAIN].pop(_RUNTIME_KEY, None)
    restarted = await async_get_quiet_hours(hass)
    await hass.async_block_till_done()

    restarted_active = restarted.active
    assert restarted_active is not None
    assert restarted_active["period_started_at"] == first_period_id
    assert restarted_active["controls"][media_player_id][
        "original_value"
    ] == pytest.approx(0.60)
    assert restarted_active["controls"][wake_sound_id]["original_value"] is True

    restarted_media_state = hass.states.get(media_player_id)
    restarted_wake_state = hass.states.get(wake_sound_id)
    assert restarted_media_state is not None
    assert restarted_wake_state is not None
    assert restarted_media_state.attributes["volume_level"] == pytest.approx(0.20)
    assert restarted_wake_state.state == "off"
    restarted_state = hass.states.get(_STATE_ENTITY_ID)
    assert restarted_state is not None
    assert restarted_state.state == "on"

    # Disabling the schedule uses the same conditional restoration path as the end
    # boundary and must return both controls to their pre-Quiet-Hours values.
    disabled = restarted.config.as_dict()
    disabled["enabled"] = False
    await restarted.async_update_config(disabled)
    await hass.async_block_till_done()

    final_media_state = hass.states.get(media_player_id)
    final_wake_state = hass.states.get(wake_sound_id)
    assert final_media_state is not None
    assert final_wake_state is not None
    assert final_media_state.attributes["volume_level"] == pytest.approx(0.60)
    assert final_wake_state.state == "on"
    assert restarted.active is None

    final_state = hass.states.get(_STATE_ENTITY_ID)
    assert final_state is not None
    assert final_state.state == "off"
    assert final_state.attributes["enabled"] is False


@pytest.mark.asyncio
async def test_real_ha_clock_callbacks_activate_rediscover_and_restore(
    hass: HomeAssistant,
) -> None:
    """Prove HA's clock listeners drive start, rediscovery, and end transitions."""
    _satellite_id, media_player_id, wake_sound_id = _install_satellite_entities(hass)
    _install_control_services(hass)

    now = dt_util.now()
    start_at = (now + timedelta(minutes=2)).replace(second=0, microsecond=0)
    end_at = (now + timedelta(minutes=10)).replace(second=0, microsecond=0)

    manager = await async_get_quiet_hours(hass)
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

    # Do not call async_reconcile directly: crossing the configured wall-clock
    # boundary must invoke the listener registered by _reschedule().
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

    # A satellite that appears after Quiet Hours starts should remain untouched
    # until the periodic five-minute discovery callback performs a reconciliation.
    _kitchen_satellite_id, kitchen_media_id, kitchen_wake_id = _install_satellite_entities(
        hass,
        slug="kitchen",
        name="Kitchen Voice",
        volume=0.75,
        wake="on",
    )
    kitchen_media = hass.states.get(kitchen_media_id)
    kitchen_wake = hass.states.get(kitchen_wake_id)
    assert kitchen_media is not None
    assert kitchen_wake is not None
    assert kitchen_media.attributes["volume_level"] == pytest.approx(0.75)
    assert kitchen_wake.state == "on"

    async_fire_time_changed(
        hass,
        (start_at + timedelta(minutes=5)).astimezone(UTC),
    )
    await hass.async_block_till_done()

    kitchen_media = hass.states.get(kitchen_media_id)
    kitchen_wake = hass.states.get(kitchen_wake_id)
    assert kitchen_media is not None
    assert kitchen_wake is not None
    assert kitchen_media.attributes["volume_level"] == pytest.approx(0.20)
    assert kitchen_wake.state == "off"

    # The configured end boundary must restore every control acquired during the
    # occurrence, including the satellite discovered after the period began.
    async_fire_time_changed(hass, end_at.astimezone(UTC))
    await hass.async_block_till_done()

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
    assert media_state.attributes["volume_level"] == pytest.approx(0.60)
    assert wake_state.state == "on"
    assert kitchen_media.attributes["volume_level"] == pytest.approx(0.75)
    assert kitchen_wake.state == "on"
    assert quiet_state.state == "off"
    assert manager.active is None
