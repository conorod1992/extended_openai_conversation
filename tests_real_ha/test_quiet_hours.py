"""Real Home Assistant acceptance coverage for global Quiet Hours."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.extended_openai_conversation_responses import quiet_hours
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    _config_from_data,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    SatelliteCapabilities,
)
from homeassistant.core import HomeAssistant, State


def _capabilities() -> list[SatelliteCapabilities]:
    return [
        SatelliteCapabilities(
            satellite_entity_id="assist_satellite.bedroom",
            name="Bedroom Voice",
            device_id="device-bedroom",
            media_player_entity_id="media_player.bedroom",
            wake_sound_entity_id="switch.bedroom_wake_sound",
            media_player_source="auto",
            wake_sound_source="auto",
        )
    ]


def _manager(hass: HomeAssistant) -> QuietHoursManager:
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    manager._unsubscribers = []
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.2,
            "wake_sound": "off",
        }
    )
    return manager


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def _install_control_services(hass: HomeAssistant):
    volume_calls: list[tuple[str, float]] = []
    switch_calls: list[tuple[str, bool]] = []

    async def volume_set(call) -> None:
        entity_id = call.data["entity_id"]
        volume = float(call.data["volume_level"])
        volume_calls.append((entity_id, volume))
        current = hass.states.get(entity_id)
        attributes = dict(current.attributes) if current else {}
        attributes["volume_level"] = volume
        hass.states.async_set(
            entity_id, current.state if current else "idle", attributes
        )

    async def switch_on(call) -> None:
        entity_id = call.data["entity_id"]
        switch_calls.append((entity_id, True))
        hass.states.async_set(entity_id, "on")

    async def switch_off(call) -> None:
        entity_id = call.data["entity_id"]
        switch_calls.append((entity_id, False))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("media_player", "volume_set", volume_set)
    hass.services.async_register("switch", "turn_on", switch_on)
    hass.services.async_register("switch", "turn_off", switch_off)
    return volume_calls, switch_calls


def _seed(hass: HomeAssistant, *, volume: float = 0.55, wake: str = "on") -> None:
    hass.states.async_set(
        "assist_satellite.bedroom",
        "idle",
        {"friendly_name": "Bedroom Voice"},
    )
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": volume})
    hass.states.async_set("switch.bedroom_wake_sound", wake)


async def test_real_ha_quiet_hours_applies_and_restores_owned_controls(
    hass: HomeAssistant, monkeypatch
) -> None:
    await hass.config.async_set_time_zone("UTC")
    monkeypatch.setattr(
        quiet_hours,
        "discover_satellite_capabilities",
        lambda *_: _capabilities(),
    )
    _seed(hass)
    manager = _manager(hass)
    volume_calls, switch_calls = await _install_control_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.2
    assert _state(hass, "switch.bedroom_wake_sound").state == "off"
    quiet_state = _state(hass, manager.snapshot()["state_entity_id"])
    assert quiet_state.state == "on"
    assert quiet_state.attributes["max_volume"] == 0.2
    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]

    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.55
    assert _state(hass, "switch.bedroom_wake_sound").state == "on"
    assert _state(hass, manager.snapshot()["state_entity_id"]).state == "off"
    assert volume_calls == [
        ("media_player.bedroom", 0.2),
        ("media_player.bedroom", 0.55),
    ]
    assert switch_calls == [
        ("switch.bedroom_wake_sound", False),
        ("switch.bedroom_wake_sound", True),
    ]


async def test_real_ha_manual_changes_opt_out_of_restore(
    hass: HomeAssistant, monkeypatch
) -> None:
    await hass.config.async_set_time_zone("UTC")
    monkeypatch.setattr(
        quiet_hours,
        "discover_satellite_capabilities",
        lambda *_: _capabilities(),
    )
    _seed(hass)
    manager = _manager(hass)
    volume_calls, switch_calls = await _install_control_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})
    hass.states.async_set("switch.bedroom_wake_sound", "on")

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))
    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.35
    assert _state(hass, "switch.bedroom_wake_sound").state == "on"
    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]


async def test_real_ha_ceiling_never_raises_and_manual_later_change_is_respected(
    hass: HomeAssistant, monkeypatch
) -> None:
    await hass.config.async_set_time_zone("UTC")
    monkeypatch.setattr(
        quiet_hours,
        "discover_satellite_capabilities",
        lambda *_: _capabilities(),
    )
    _seed(hass, volume=0.1, wake="off")
    manager = _manager(hass)
    volume_calls, switch_calls = await _install_control_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.1
    assert volume_calls == []
    assert switch_calls == []

    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.45})
    hass.states.async_set("switch.bedroom_wake_sound", "on")
    await manager.async_reconcile(now=datetime(2026, 9, 11, 23, 0, tzinfo=UTC))

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.45
    assert _state(hass, "switch.bedroom_wake_sound").state == "on"
    assert volume_calls == []
    assert switch_calls == []
