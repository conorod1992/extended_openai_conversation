"""Tests for global Assist satellite Quiet Hours management."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours_runtime
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
        "max_volume": 0.2,
        "wake_sound": "off",
        "overrides": {},
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


def _entity(
    entity_id: str,
    domain: str,
    device_id: str,
    *,
    platform: str = "esphome",
    original_name: str | None = None,
):
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        device_id=device_id,
        disabled_by=None,
        platform=platform,
        original_name=original_name,
        name=None,
    )


def _install_registry(monkeypatch, *rooms: str) -> None:
    entries = {}
    for room in rooms:
        device_id = f"device-{room}"
        entries[f"assist_satellite.{room}"] = _entity(
            f"assist_satellite.{room}",
            "assist_satellite",
            device_id,
            original_name="Assist satellite",
        )
        entries[f"media_player.{room}"] = _entity(
            f"media_player.{room}",
            "media_player",
            device_id,
            original_name="Media Player",
        )
        entries[f"switch.{room}_wake_sound"] = _entity(
            f"switch.{room}_wake_sound",
            "switch",
            device_id,
            original_name="Wake sound",
        )
    registry = SimpleNamespace(entities=entries)
    monkeypatch.setattr(quiet_hours_runtime.er, "async_get", lambda _hass: registry)


def _seed_room(hass, room: str, *, volume=0.55, wake="on") -> None:
    hass.states.async_set(
        f"assist_satellite.{room}",
        "idle",
        {"friendly_name": f"{room.title()} Voice"},
    )
    hass.states.async_set(
        f"media_player.{room}", "idle", {"volume_level": volume}
    )
    hass.states.async_set(f"switch.{room}_wake_sound", wake)


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


def test_config_uses_ceiling_tri_state_and_migrates_prototype_names() -> None:
    config = _config()
    assert config.max_volume == 0.2
    assert config.wake_sound == "off"

    migrated = _config_from_data(
        {"volume_level": 0.3, "wake_sound_enabled": True}
    )
    assert migrated.max_volume == 0.3
    assert migrated.wake_sound == "on"

    with pytest.raises(ValueError, match="between 0 and 1"):
        _config_from_data({"max_volume": 1.2})
    with pytest.raises(ValueError, match="on, off, or unchanged"):
        _config_from_data({"wake_sound": "sometimes"})
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
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")

    discovered = discover_satellite_capabilities(hass, _config())

    assert len(discovered) == 1
    assert discovered[0].media_player_entity_id == "media_player.bedroom"
    assert discovered[0].media_player_source == "auto"
    assert discovered[0].wake_sound_entity_id == "switch.bedroom_wake_sound"
    assert discovered[0].wake_sound_source == "auto"


def test_manual_mapping_overrides_auto_discovery(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    hass.states.async_set("media_player.manual", "idle", {"volume_level": 0.7})
    hass.states.async_set("switch.manual_wake", "on")

    discovered = discover_satellite_capabilities(
        hass,
        _config(
            overrides={
                "assist_satellite.bedroom": {
                    "media_player_entity_id": "media_player.manual",
                    "wake_sound_entity_id": "switch.manual_wake",
                }
            }
        ),
    )

    assert discovered[0].media_player_entity_id == "media_player.manual"
    assert discovered[0].media_player_source == "manual"
    assert discovered[0].wake_sound_entity_id == "switch.manual_wake"
    assert discovered[0].wake_sound_source == "manual"


async def test_ceiling_lowers_only_satellites_above_limit(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom", "kitchen")
    _seed_room(hass, "bedroom", volume=0.55)
    _seed_room(hass, "kitchen", volume=0.1)
    manager = _manager(hass)
    manager._config = _config(wake_sound="unchanged")
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == []
    assert "media_player.bedroom" in manager.active["controls"]
    assert "media_player.kitchen" not in manager.active["controls"]
    assert hass.states["media_player.kitchen"].attributes["volume_level"] == 0.1


async def test_active_entity_tracks_schedule_even_when_nothing_changes(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom", volume=0.1, wake="off")
    manager = _manager(hass)
    manager._config = _config()
    await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    state = hass.states["binary_sensor.extended_openai_quiet_hours"]
    assert state.state == "on"
    assert state.attributes["max_volume"] == 0.2
    assert state.attributes["wake_sound"] == "off"
    assert manager.active is not None
    assert manager.active["controls"] == {}

    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))
    assert hass.states["binary_sensor.extended_openai_quiet_hours"].state == "off"


async def test_quiet_hours_persists_ownership_before_mutation(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _manager(hass)
    manager._config = _config()
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
    saves = manager._store.async_save.await_args_list
    assert any(
        "media_player.bedroom" in call.args[0].get("active", {}).get("controls", {})
        for call in saves
    )


async def test_restart_same_period_preserves_originals_and_does_not_reapply(
    monkeypatch, hass
) -> None:
    _install_registry(monkeypatch, "bedroom")
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
    _seed_room(hass, "bedroom", volume=0.2, wake="off")
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))

    assert volume_calls == []
    assert switch_calls == []
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.55


async def test_manual_changes_are_not_reapplied_or_restored(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    manager = _manager(hass)
    manager._config = _config()
    _seed_room(hass, "bedroom")
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})
    hass.states.async_set("switch.bedroom_wake_sound", "on")

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))
    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]
    assert hass.states["media_player.bedroom"].attributes["volume_level"] == 0.35
    assert hass.states["switch.bedroom_wake_sound"].state == "on"
    assert manager.active is None


async def test_end_restores_controls_still_owned(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    manager = _manager(hass)
    manager._config = _config()
    _seed_room(hass, "bedroom")
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


async def test_same_period_discovers_new_satellite_without_reclaiming_manual_change(
    monkeypatch, hass
) -> None:
    _install_registry(monkeypatch, "bedroom")
    manager = _manager(hass)
    manager._config = _config(wake_sound="unchanged")
    _seed_room(hass, "bedroom")
    volume_calls, _ = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.35})

    _install_registry(monkeypatch, "bedroom", "kitchen")
    _seed_room(hass, "kitchen", volume=0.6)
    await manager.async_reconcile(now=datetime(2026, 9, 11, 23, 0, tzinfo=UTC))

    assert volume_calls == [
        ("media_player.bedroom", 0.2),
        ("media_player.kitchen", 0.2),
    ]
    assert hass.states["media_player.bedroom"].attributes["volume_level"] == 0.35
    assert manager.active["controls"]["media_player.kitchen"]["original_value"] == 0.6
