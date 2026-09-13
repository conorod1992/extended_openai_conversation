"""Behavioural coverage for the global Quiet Hours runtime and actions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours_runtime
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    SERVICE_DISABLE_QUIET_HOURS,
    SERVICE_ENABLE_QUIET_HOURS,
    QuietHoursManager,
    _register_quiet_hours_actions,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    _config_from_data,
)


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


def _manager(hass) -> QuietHoursManager:
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    manager._registered_state_entity_id = "binary_sensor.extended_openai_quiet_hours"
    manager._unsubscribers = []
    return manager


def _entry(entity_id: str, domain: str, device_id: str, *, name: str):
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        device_id=device_id,
        disabled_by=None,
        platform="esphome",
        original_name=name,
        name=None,
    )


def _install_registry(monkeypatch, *rooms: str) -> None:
    entries = {}
    for room in rooms:
        device_id = f"device-{room}"
        entries[f"assist_satellite.{room}"] = _entry(
            f"assist_satellite.{room}", "assist_satellite", device_id, name="Assist satellite"
        )
        entries[f"media_player.{room}"] = _entry(
            f"media_player.{room}", "media_player", device_id, name="Media Player"
        )
        entries[f"switch.{room}_wake_sound"] = _entry(
            f"switch.{room}_wake_sound", "switch", device_id, name="Wake sound"
        )
    registry = SimpleNamespace(entities=entries)
    monkeypatch.setattr(quiet_hours_runtime.er, "async_get", lambda _hass: registry)


def _seed_room(hass, room: str, *, volume: float = 0.55, wake: str = "on") -> None:
    hass.states.async_set(
        f"assist_satellite.{room}", "idle", {"friendly_name": f"{room.title()} Voice"}
    )
    hass.states.async_set(
        f"media_player.{room}", "idle", {"volume_level": volume}
    )
    hass.states.async_set(f"switch.{room}_wake_sound", wake)


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

    async def turn_on(call) -> None:
        entity_id = call.data["entity_id"]
        switch_calls.append((entity_id, True))
        hass.states.async_set(entity_id, "on")

    async def turn_off(call) -> None:
        entity_id = call.data["entity_id"]
        switch_calls.append((entity_id, False))
        hass.states.async_set(entity_id, "off")

    hass.services.async_register("media_player", "volume_set", volume_set)
    hass.services.async_register("switch", "turn_on", turn_on)
    hass.services.async_register("switch", "turn_off", turn_off)
    return volume_calls, switch_calls


@pytest.mark.asyncio
async def test_ceiling_only_lowers_louder_satellites(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom", "kitchen")
    _seed_room(hass, "bedroom", volume=0.55)
    _seed_room(hass, "kitchen", volume=0.10)
    manager = _manager(hass)
    manager._config = _config(wake_sound="unchanged")
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == []
    assert hass.states["media_player.kitchen"].attributes["volume_level"] == 0.10
    assert "media_player.bedroom" in manager.active["controls"]
    assert "media_player.kitchen" not in manager.active["controls"]
    assert "media_player.kitchen" in manager.active["observed_controls"]


@pytest.mark.asyncio
async def test_schedule_entity_turns_on_even_when_policy_is_noop(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom", volume=0.10, wake="off")
    manager = _manager(hass)
    manager._config = _config()
    await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    state = hass.states["binary_sensor.extended_openai_quiet_hours"]
    assert state.state == "on"
    assert state.attributes["max_volume"] == 0.2
    assert state.attributes["wake_sound"] == "off"
    assert manager.active["controls"] == {}

    await manager.async_reconcile(now=datetime(2026, 9, 12, 7, 0, tzinfo=UTC))
    assert hass.states["binary_sensor.extended_openai_quiet_hours"].state == "off"


@pytest.mark.asyncio
async def test_ownership_is_saved_before_mutating_controls(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _manager(hass)
    manager._config = _config()
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert volume_calls == [("media_player.bedroom", 0.2)]
    assert switch_calls == [("switch.bedroom_wake_sound", False)]
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.55
    assert manager.active["controls"]["switch.bedroom_wake_sound"]["original_value"] is True
    saves = manager._store.async_save.await_args_list
    assert any(
        "media_player.bedroom" in call.args[0].get("active", {}).get("controls", {})
        for call in saves
    )


@pytest.mark.asyncio
async def test_restart_same_period_preserves_originals_without_reapplying(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom", volume=0.2, wake="off")
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
        "observed_controls": [
            "media_player.bedroom",
            "switch.bedroom_wake_sound",
        ],
    }
    volume_calls, switch_calls = await _install_services(hass)

    await manager.async_reconcile(now=datetime(2026, 9, 12, 2, 0, tzinfo=UTC))

    assert volume_calls == []
    assert switch_calls == []
    assert manager.active["controls"]["media_player.bedroom"]["original_value"] == 0.55


@pytest.mark.asyncio
async def test_manual_changes_are_not_reclaimed_or_restored(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _manager(hass)
    manager._config = _config()
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


@pytest.mark.asyncio
async def test_end_restores_controls_that_are_still_owned(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _manager(hass)
    manager._config = _config()
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


@pytest.mark.asyncio
async def test_periodic_discovery_adds_new_satellite_but_not_manual_change(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _manager(hass)
    manager._config = _config(wake_sound="unchanged")
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


@pytest.mark.asyncio
async def test_failed_mutation_releases_ownership_for_retry(monkeypatch, hass) -> None:
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom")
    manager = _manager(hass)
    manager._config = _config(wake_sound="unchanged")
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("boom"))

    await manager.async_reconcile(now=datetime(2026, 9, 11, 22, 0, tzinfo=UTC))

    assert "media_player.bedroom" not in manager.active["controls"]
    assert "media_player.bedroom" not in manager.active["observed_controls"]


@pytest.mark.asyncio
async def test_async_set_enabled_preserves_policy(monkeypatch, hass) -> None:
    manager = _manager(hass)
    manager._config = _config(
        enabled=False,
        start="21:30",
        end="06:15",
        max_volume=0.17,
        wake_sound="unchanged",
    )
    manager.async_update_config = AsyncMock(return_value={"active": False})

    result = await manager.async_set_enabled(True)

    assert result == {"active": False}
    submitted = manager.async_update_config.await_args.args[0]
    assert submitted["enabled"] is True
    assert submitted["start"] == "21:30"
    assert submitted["end"] == "06:15"
    assert submitted["max_volume"] == 0.17
    assert submitted["wake_sound"] == "unchanged"
    with pytest.raises(ValueError, match="boolean"):
        await manager.async_set_enabled("yes")


@pytest.mark.asyncio
async def test_enable_disable_actions_are_global_and_idempotently_registered(hass) -> None:
    manager = _manager(hass)
    manager._config = _config(enabled=False)
    manager.async_set_enabled = AsyncMock()
    hass.data.setdefault(DOMAIN, {})["quiet_hours_manager"] = manager

    _register_quiet_hours_actions(hass)
    _register_quiet_hours_actions(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_ENABLE_QUIET_HOURS)
    assert hass.services.has_service(DOMAIN, SERVICE_DISABLE_QUIET_HOURS)

    await hass.services.async_call(DOMAIN, SERVICE_ENABLE_QUIET_HOURS, {}, blocking=True)
    manager.async_set_enabled.assert_awaited_once_with(True)

    await hass.services.async_call(DOMAIN, SERVICE_DISABLE_QUIET_HOURS, {}, blocking=True)
    manager.async_set_enabled.assert_awaited_with(False)


@pytest.mark.asyncio
async def test_shutdown_unsubscribes_and_removes_state(hass) -> None:
    manager = _manager(hass)
    unsub = AsyncMock()
    manager._unsubscribers = [unsub]
    hass.states.async_set("binary_sensor.extended_openai_quiet_hours", "off")

    await manager.async_shutdown()

    unsub.assert_called_once_with()
    assert hass.states.get("binary_sensor.extended_openai_quiet_hours") is None
