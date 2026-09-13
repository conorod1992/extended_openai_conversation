"""Tests for global Assist satellite Quiet Hours management."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    _config_from_data,
    discover_satellite_capabilities,
    quiet_period_for,
)


def _manager(hass) -> QuietHoursManager:
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    manager._unsubscribers = []
    return manager


def _config(**overrides):
    value = {
        "enabled": True,
        "start": "22:00",
        "end": "07:00",
        "volume_level": 0.2,
        "wake_sound_enabled": False,
        "overrides": {
            "assist_satellite.bedroom": {
                "media_player_entity_id": "media_player.bedroom",
                "wake_sound_entity_id": "switch.bedroom_wake_sound",
            }
        },
    }
    value.update(overrides)
    return _config_from_data(value)


async def _install_services(hass):
    volume_calls: list[tuple[str, float]] = []
    switch_calls: list[tuple[str, bool]] = []

    async def volume_set(call) -> None:
        entity_id = call.data["entity_id"]
        volume = float(call.data["volume_level"])
        volume_calls.append((entity_id, volume))
        current = hass.states.get(entity_id)
        attrs = dict(current.attributes) if current else {}
        attrs["volume_level"] = volume
        hass.states.async_set(entity_id, current.state if current else "idle", attrs)

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


def _seed_bedroom(hass, *, volume=0.55, wake="on") -> None:
    hass.states.async_set(
        "assist_satellite.bedroom", "idle", {"friendly_name": "Bedroom Voice"}
    )
    hass.states.async_set(
        "media_player.bedroom", "idle", {"volume_level": volume}
    )
    hass.states.async_set("switch.bedroom_wake_sound", wake)


def test_quiet_period_for_overnight_window() -> None:
    before_midnight = quiet_period_for(
        datetime(2026, 9, 11, 23, 30, tzinfo=UTC), "22:00", "07:00"
    )
    after_midnight = quiet_period_for(
        datetime(2026, 9, 12, 2, 30, tzinfo=UTC), "22:00", "07:00"
    )

    assert before_midnight is not None
    assert after_midnight is not None
    assert before_midnight.start == datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert after_midnight.start == before_midnight.start
    assert after_midnight.end == datetime(2026, 9, 12, 7, 0, tzinfo=UTC)
    assert quiet_period_for(
        datetime(2026, 9, 12, 7, 0, tzinfo=UTC), "22:00", "07:00"
    ) is None


def test_config_validates_global_policy_and_manual_mappings() -> None:
    config = _config()
    assert config.volume_level == 0.2
    assert config.wake_sound_enabled is False
    assert config.override_for("assist_satellite.bedroom").media_player_entity_id == (
        "media_player.bedroom"
    )

    with pytest.raises(ValueError, match="between 0 and 1"):
        _config_from_data({"volume_level": 1.2})
    with pytest.raises(ValueError, match="assist_satellite"):
        _config_from_data({"overrides": {"sensor.bad": {}}})
    with pytest.raises(ValueError, match="media_player"):
        _config_from_data(
            {
                "overrides": {
                    "assist_satellite.bedroom": {
                        "media_player_entity_id": "light.wrong"
                    }
                }
            }
        )


def test_discovery_uses_same_device_and_voice_pe_wake_sound(monkeypatch, hass) -> None:
    entries = {
        "assist_satellite.bedroom": SimpleNamespace(
            entity_id="assist_satellite.bedroom",
            domain="assist_satellite",
            device_id="device-1",
            disabled_by=None,
            platform="esphome",
            original_name="Assist satellite",
            name=None,
        ),
        "media_player.bedroom": SimpleNamespace(
            entity_id="media_player.bedroom",
            domain="media_player",
            device_id="device-1",
            disabled_by=None,
            platform="esphome",
            original_name="Media Player",
            name=None,
        ),
        "switch.bedroom_wake_sound": SimpleNamespace(
            entity_id="switch.bedroom_wake_sound",
            domain="switch",
            device_id="device-1",
            disabled_by=None,
            platform="esphome",
            original_name="Wake sound",
            name=None,
        ),
        "media_player.other": SimpleNamespace(
            entity_id="media_player.other",
            domain="media_player",
            device_id="device-2",
            disabled_by=None,
            platform="esphome",
            original_name="Media Player",
            name=None,
        ),
    }
    registry = SimpleNamespace(
        entities=entries,
        async_get=lambda entity_id: entries.get(entity_id),
    )
    monkeypatch.setattr(quiet_hours.er, "async_get", lambda _hass: registry)
    hass.states.async_set(
        "assist_satellite.bedroom", "idle", {"friendly_name": "Bedroom Voice"}
    )
    hass.states.async_set(
        "media_player.bedroom", "idle", {"volume_level": 0.5}
    )

    discovered = discover_satellite_capabilities(
        hass,
        _config_from_data(
            {
                "enabled": True,
                "start": "22:00",
                "end": "07:00",
                "volume_level": 0.2,
                "wake_sound_enabled": False,
            }
        ),
    )

    assert len(discovered) == 1
    assert discovered[0].media_player_entity_id == "media_player.bedroom"
    assert discovered[0].media_player_source == "auto"
    assert discovered[0].wake_sound_entity_id == "switch.bedroom_wake_sound"
    assert discovered[0].wake_sound_source == "auto"


async def test_quiet_hours_applies_volume_and_wake_sound_after_persisting(hass) -> None:
    manager = _manager(hass)
    manager._config = _config()
    _seed_bedroom(hass)
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]
    assert manager.active["period_started_at"] == "2026-09-11T22:00:00+00:00"
    assert manager.active["controls"]["media_player.bedroom"] == {
        "kind": "volume",
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": 0.55,
        "quiet_value": 0.2,
    }
    assert manager.active["controls"]["switch.bedroom_wake_sound"][
        "original_value"
    ] is True

    # Ownership is saved before the first external volume mutation.
    saves = manager._store.async_save.await_args_list
    assert any(
        "media_player.bedroom" in call.args[0].get("active", {}).get("controls", {})
        for call in saves
    )


async def test_restart_same_period_preserves_originals_and_does_not_reapply(hass) -> None:
    manager = _manager(hass)
    manager._config = _config()
    manager._active = {
        "period_started_at": "2026-09-11T22:00:00+00:00",
        "period_ends_at": "2026-09-12T07:00:00+00:00",
        "applied_at": "2026-09-11T22:00:00+00:00",
        "controls": {
            "media_player.bedroom": {
                "kind": "volume",
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": 0.55,
                "quiet_value": 0.2,
            },
            "switch.bedroom_wake_sound": {
                "kind": "switch",
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": True,
                "quiet_value": False,
            },
        },
    }
    _seed_bedroom(hass, volume=0.2, wake="off")
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))

    assert volume_calls == []
    assert switch_calls == []
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.55


async def test_manual_changes_are_not_reapplied_or_restored(hass) -> None:
    manager = _manager(hass)
    manager._config = _config()
    _seed_bedroom(hass)
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})
    hass.states.async_set("switch.bedroom_wake_sound", "on")

    # Reconciliation in the same period respects both manual changes.
    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))
    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]
    assert hass.states["media_player.bedroom"].attributes["volume_level"] == 0.35
    assert hass.states["switch.bedroom_wake_sound"].state == "on"
    assert manager.active is None


async def test_end_restores_controls_still_owned(hass) -> None:
    manager = _manager(hass)
    manager._config = _config()
    _seed_bedroom(hass)
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert volume_calls == [
        ("media_player.bedroom", 0.2),
        ("media_player.bedroom", 0.55),
    ]
    assert switch_calls == [
        ("switch.bedroom_wake_sound", False),
        ("switch.bedroom_wake_sound", True),
    ]
    assert manager.active is None


async def test_same_period_reconcile_can_pick_up_new_satellite_without_reclaiming_old(hass) -> None:
    manager = _manager(hass)
    manager._config = _config(
        overrides={
            "assist_satellite.bedroom": {
                "media_player_entity_id": "media_player.bedroom",
            },
            "assist_satellite.kitchen": {
                "media_player_entity_id": "media_player.kitchen",
            },
        }
    )
    _seed_bedroom(hass)
    volume_calls, _ = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})

    hass.states.async_set(
        "assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen Voice"}
    )
    hass.states.async_set(
        "media_player.kitchen", "idle", {"volume_level": 0.6}
    )
    await manager.async_reconcile(now=datetime(2026, 9, 11, 23, 0, tzinfo=UTC))

    assert volume_calls == [
        ("media_player.bedroom", 0.2),
        ("media_player.kitchen", 0.2),
    ]
    assert hass.states["media_player.bedroom"].attributes["volume_level"] == 0.35
    assert manager.active["controls"]["media_player.kitchen"]["original_value"] == 0.6
