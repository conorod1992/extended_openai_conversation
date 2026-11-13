"""Guest Mode persistence uncertainty must mark the HA sensor unavailable."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
)
from custom_components.extended_openai_conversation_responses.sensor import GuestModeSensor
from homeassistant.exceptions import HomeAssistantError
from tests.test_sensor_coverage import _subentry


def _manager(hass):
    manager = GuestModeManager(hass, "entry", "agent")
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={"schedule": None}),
        async_save=AsyncMock(),
    )
    return manager


def _status_sensor(manager):
    return GuestModeSensor(_subentry(), manager)


async def test_sensor_healthy_inactive_scheduled_active_and_indefinite(hass, monkeypatch):
    manager = _manager(hass)
    sensor = _status_sensor(manager)
    assert not sensor.available
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}
    await manager.async_initialize()
    assert sensor.available
    assert sensor.native_value == "inactive"

    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.dt_util.utcnow",
        lambda: now,
    )
    await manager.async_update_trusted(
        active_from="2026-11-02T12:00:00+00:00",
        active_until="2026-11-03T12:00:00+00:00",
        now=now,
    )
    assert sensor.native_value == "scheduled"
    assert sensor.extra_state_attributes["scheduled"] is True
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.guest_mode.dt_util.utcnow",
        lambda: datetime(2026, 11, 2, 13, tzinfo=UTC),
    )
    assert sensor.native_value == "active"
    await manager.async_update_trusted(indefinite=True, now=now)
    assert sensor.native_value == "active_indefinitely"


async def test_failed_save_and_failed_readback_notify_unavailable_once_then_recover(hass):
    manager = _manager(hass)
    await manager.async_initialize()
    await manager.async_update_trusted(indefinite=True)
    committed = manager.schedule
    sensor = _status_sensor(manager)
    listener = Mock()
    remove = manager.async_add_listener(listener)

    manager._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    manager._store.async_load = AsyncMock(side_effect=OSError("readback failed"))
    with pytest.raises(OSError):
        await manager.async_disable_trusted()
    assert not manager.authoritative_available
    assert not sensor.available
    with pytest.raises(HomeAssistantError, match="persistence is unavailable"):
        manager.status()
    for _ in range(25):
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}
        assert sensor.available is False
    listener.assert_called_once_with()
    assert manager.schedule is committed

    manager._store.async_load = AsyncMock(
        return_value={"schedule": asdict(committed)}
    )
    await manager.async_initialize()
    assert manager.authoritative_available
    assert sensor.available
    assert sensor.native_value == "active_indefinitely"
    assert sensor.extra_state_attributes["currently_active"] is True
    assert listener.call_count == 2
    await manager.async_initialize()
    assert listener.call_count == 2
    remove()


async def test_sensor_unload_and_manager_replacement_drop_old_notifications(hass):
    old = _manager(hass)
    await old.async_initialize()
    first = _status_sensor(old)
    calls = Mock()
    remove = old.async_add_listener(calls)
    replacement = _manager(hass)
    await replacement.async_initialize()
    second = _status_sensor(replacement)
    assert first.available and second.available
    remove()  # HA's async_on_remove owns this callback on platform unload.
    await old.async_update_trusted(indefinite=True)
    calls.assert_not_called()
    await replacement.async_update_trusted(indefinite=True)
    assert first.native_value == "active_indefinitely"
    assert second.native_value == "active_indefinitely"
    assert old is not replacement


def test_sensor_does_not_call_status_during_uncertain_generation(hass):
    manager = _manager(hass)
    manager._initialized = False
    manager._persistence_unavailable = True
    manager.status = Mock(side_effect=HomeAssistantError("uncertain"))
    sensor = _status_sensor(manager)
    for _ in range(25):
        assert sensor.available is False
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}
    manager.status.assert_not_called()
