"""Real Home Assistant resilience coverage for global Quiet Hours."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    SERVICE_DISABLE_QUIET_HOURS,
    SERVICE_ENABLE_QUIET_HOURS,
    QuietHoursManager,
    _config_from_data,
    async_get_quiet_hours,
)
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    SatelliteCapabilities,
)
from homeassistant.core import Context, HomeAssistant, ServiceCall, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util


def _capability(room: str, *, wake_sound: bool = True) -> SatelliteCapabilities:
    return SatelliteCapabilities(
        satellite_entity_id=f"assist_satellite.{room}",
        name=f"{room.title()} Voice",
        device_id=f"device-{room}",
        media_player_entity_id=f"media_player.{room}",
        wake_sound_entity_id=f"switch.{room}_wake_sound" if wake_sound else None,
        media_player_source="auto",
        wake_sound_source="auto" if wake_sound else None,
    )


def _manager(hass: HomeAssistant) -> QuietHoursManager:
    manager = QuietHoursManager(hass)
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    manager._initialized = True
    manager._unsubscribers = []
    return manager


def _active_window() -> tuple[str, str]:
    now = dt_util.now()
    return (
        (now - timedelta(hours=1)).strftime("%H:%M"),
        (now + timedelta(hours=1)).strftime("%H:%M"),
    )


def _seed(
    hass: HomeAssistant,
    room: str,
    *,
    volume: float,
    wake: str = "on",
) -> None:
    hass.states.async_set(
        f"assist_satellite.{room}",
        "idle",
        {"friendly_name": f"{room.title()} Voice"},
    )
    hass.states.async_set(
        f"media_player.{room}",
        "idle",
        {"volume_level": volume},
    )
    hass.states.async_set(f"switch.{room}_wake_sound", wake)


def _state(hass: HomeAssistant, entity_id: str) -> State:
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def _install_control_services(
    hass: HomeAssistant,
    *,
    fail_volume_once: set[str] | None = None,
) -> tuple[list[tuple[str, float]], list[tuple[str, bool]]]:
    volume_calls: list[tuple[str, float]] = []
    switch_calls: list[tuple[str, bool]] = []
    remaining_failures = set(fail_volume_once or set())

    async def volume_set(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        volume = float(call.data["volume_level"])
        volume_calls.append((entity_id, volume))
        if entity_id in remaining_failures:
            remaining_failures.remove(entity_id)
            raise HomeAssistantError("temporary Quiet Hours volume failure")
        current = hass.states.get(entity_id)
        assert current is not None
        attributes = dict(current.attributes)
        attributes["volume_level"] = volume
        hass.states.async_set(entity_id, current.state, attributes)

    async def switch_on(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        switch_calls.append((entity_id, True))
        current = hass.states.get(entity_id)
        hass.states.async_set(
            entity_id,
            "on",
            dict(current.attributes) if current is not None else {},
        )

    async def switch_off(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        switch_calls.append((entity_id, False))
        current = hass.states.get(entity_id)
        hass.states.async_set(
            entity_id,
            "off",
            dict(current.attributes) if current is not None else {},
        )

    hass.services.async_register("media_player", "volume_set", volume_set)
    hass.services.async_register("switch", "turn_on", switch_on)
    hass.services.async_register("switch", "turn_off", switch_off)
    return volume_calls, switch_calls


@pytest.mark.asyncio
async def test_live_policy_replacement_restores_old_policy_before_applying_new_one(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing an active policy must preserve the original pre-quiet baseline."""
    monkeypatch.setattr(
        quiet_hours,
        "discover_satellite_capabilities",
        lambda *_: [_capability("bedroom")],
    )
    _seed(hass, "bedroom", volume=0.55, wake="on")
    volume_calls, switch_calls = await _install_control_services(hass)
    manager = _manager(hass)
    start, end = _active_window()

    await manager.async_update_config(
        {
            "enabled": True,
            "start": start,
            "end": end,
            "max_volume": 0.20,
            "wake_sound": "off",
        }
    )
    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.20
    assert _state(hass, "switch.bedroom_wake_sound").state == "off"

    await manager.async_update_config(
        {
            "enabled": True,
            "start": start,
            "end": end,
            "max_volume": 0.35,
            "wake_sound": "on",
        }
    )

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.35
    assert _state(hass, "switch.bedroom_wake_sound").state == "on"
    active = manager.active
    assert active is not None
    assert active["controls"]["media_player.bedroom"]["original_value"] == 0.55
    assert active["controls"]["media_player.bedroom"]["quiet_value"] == 0.35
    assert "switch.bedroom_wake_sound" not in active["controls"]

    disabled = manager.config.as_dict()
    disabled["enabled"] = False
    await manager.async_update_config(disabled)

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.55
    assert _state(hass, "switch.bedroom_wake_sound").state == "on"
    assert manager.active is None
    assert volume_calls == [
        ("media_player.bedroom", 0.20),
        ("media_player.bedroom", 0.55),
        ("media_player.bedroom", 0.35),
        ("media_player.bedroom", 0.55),
    ]
    assert switch_calls == [
        ("switch.bedroom_wake_sound", False),
        ("switch.bedroom_wake_sound", True),
    ]


@pytest.mark.asyncio
async def test_control_failure_isolated_to_one_satellite_and_retried_cleanly(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed control must not block siblings and must be retryable next reconcile."""
    monkeypatch.setattr(
        quiet_hours,
        "discover_satellite_capabilities",
        lambda *_: [
            _capability("bedroom", wake_sound=False),
            _capability("kitchen", wake_sound=False),
        ],
    )
    _seed(hass, "bedroom", volume=0.60)
    _seed(hass, "kitchen", volume=0.70)
    volume_calls, _switch_calls = await _install_control_services(
        hass,
        fail_volume_once={"media_player.bedroom"},
    )
    manager = _manager(hass)
    manager._config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.20,
            "wake_sound": "unchanged",
        }
    )
    local_tz = dt_util.get_time_zone(hass.config.time_zone)
    assert local_tz is not None

    await manager.async_reconcile(
        now=datetime(2026, 9, 11, 22, 0, tzinfo=local_tz)
    )

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.60
    assert _state(hass, "media_player.kitchen").attributes["volume_level"] == 0.20
    active = manager.active
    assert active is not None
    assert set(active["controls"]) == {"media_player.kitchen"}

    await manager.async_reconcile(
        now=datetime(2026, 9, 11, 22, 5, tzinfo=local_tz)
    )

    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.20
    assert _state(hass, "media_player.kitchen").attributes["volume_level"] == 0.20
    active = manager.active
    assert active is not None
    assert set(active["controls"]) == {
        "media_player.bedroom",
        "media_player.kitchen",
    }
    assert volume_calls[:3] == [
        ("media_player.bedroom", 0.20),
        ("media_player.kitchen", 0.20),
        ("media_player.bedroom", 0.20),
    ]

    await manager.async_reconcile(
        now=datetime(2026, 9, 12, 7, 0, tzinfo=local_tz)
    )
    assert _state(hass, "media_player.bedroom").attributes["volume_level"] == 0.60
    assert _state(hass, "media_player.kitchen").attributes["volume_level"] == 0.70
    assert manager.active is None


@pytest.mark.asyncio
async def test_enable_disable_services_enforce_admin_but_allow_system_automation(
    hass: HomeAssistant,
    hass_admin_user,
    hass_read_only_user,
) -> None:
    """Quiet Hours actions must enforce HA identity without blocking automations."""
    manager = await async_get_quiet_hours(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_ENABLE_QUIET_HOURS)
    assert hass.services.has_service(DOMAIN, SERVICE_DISABLE_QUIET_HOURS)
    assert manager.config.enabled is False

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ENABLE_QUIET_HOURS,
            {},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    assert manager.config.enabled is False

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ENABLE_QUIET_HOURS,
        {},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert manager.config.enabled is True

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISABLE_QUIET_HOURS,
            {},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    assert manager.config.enabled is True

    # Calls without a user context represent trusted system automations and are
    # intentionally allowed by the service contract.
    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISABLE_QUIET_HOURS,
        {},
        blocking=True,
    )
    assert manager.config.enabled is False

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ENABLE_QUIET_HOURS,
        {},
        blocking=True,
    )
    assert manager.config.enabled is True

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISABLE_QUIET_HOURS,
        {},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert manager.config.enabled is False