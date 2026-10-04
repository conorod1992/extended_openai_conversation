"""Quiet Hours restores survive temporary unavailability and save failures."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours,
    quiet_hours_runtime as runtime,
)
from tests.test_quiet_hours_runtime import _runtime_manager, _stateful_public_manager


def _active(entity_id, kind):
    return {
        "period_started_at": "2026-10-03T22:00:00+00:00",
        "period_ends_at": "2026-10-04T07:00:00+00:00",
        "controls": {
            entity_id: {
                "kind": kind,
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": 0.8 if kind == "volume" else True,
                "quiet_value": 0.2 if kind == "volume" else False,
                "application_state": "applied",
            }
        },
        "observed_controls": [entity_id],
    }


@pytest.mark.parametrize("manager_type", ["base", "public"])
@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_unavailable_control_retains_baseline_until_return(
    hass, manager_type, kind
):
    manager = (
        _stateful_public_manager(hass)
        if manager_type == "public"
        else _runtime_manager(hass)
    )
    entity_id = "media_player.bedroom" if kind == "volume" else "switch.wake_sound"
    state = SimpleNamespace(state="unavailable", attributes={"volume_level": 0.2})
    hass.states.get.side_effect = lambda _: state
    manager._active = _active(entity_id, kind)
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    await manager._async_restore_locked()
    assert manager.active["controls"][entity_id]["restoration_pending"] is True
    persisted = deepcopy(manager._store.async_save.call_args.args[0]["active"])
    manager._active = manager._normalize_active(persisted)
    assert manager.active["controls"][entity_id]["original_value"] == (
        0.8 if kind == "volume" else True
    )
    manager._async_set_volume.assert_not_awaited()
    manager._async_set_switch.assert_not_awaited()
    state.state = "idle" if kind == "volume" else "off"
    await manager._async_restore_locked()
    method = (
        manager._async_set_volume if kind == "volume" else manager._async_set_switch
    )
    method.assert_awaited_once_with(entity_id, 0.8 if kind == "volume" else True)
    assert manager.active is None


async def test_failed_configuration_save_leaves_devices_and_active_baseline_untouched(
    hass,
):
    manager = _stateful_public_manager(hass)
    manager._config = runtime.QuietHoursConfig(enabled=True)
    manager._active = _active("media_player.bedroom", "volume")
    before = deepcopy(manager.active)
    manager._store.async_save.side_effect = OSError("settings not saved")
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    manager._reschedule = Mock()
    with pytest.raises(OSError, match="not saved"):
        await manager.async_update_config({"enabled": False})
    assert manager.config.enabled
    assert manager.active == before
    manager._async_set_volume.assert_not_awaited()
    manager._async_set_switch.assert_not_awaited()
    manager._reschedule.assert_not_called()


async def test_disabled_schedule_keeps_only_restoration_retry_timer(hass, monkeypatch):
    manager = _stateful_public_manager(hass)
    manager._publish_state = Mock()
    manager._active = _active("media_player.bedroom", "volume")
    manager._active["controls"]["media_player.bedroom"]["restoration_pending"] = True
    interval = Mock(return_value=Mock())
    clock = Mock()
    monkeypatch.setattr(runtime, "async_track_time_interval", interval)
    monkeypatch.setattr(runtime, "async_track_time_change", clock)
    manager._reschedule()
    interval.assert_called_once()
    clock.assert_not_called()


async def test_old_period_pending_baseline_survives_next_period(hass, monkeypatch):
    manager = _stateful_public_manager(hass)
    manager._config = runtime.QuietHoursConfig(enabled=True)
    manager._publish_state = Mock()
    manager._active = _active("media_player.bedroom", "volume")
    hass.states.get.side_effect = lambda _: None
    monkeypatch.setattr(quiet_hours, "discover_satellite_capabilities", lambda *_: [])
    await manager.async_reconcile(now=datetime(2026, 10, 4, 23, tzinfo=UTC))
    assert manager.active["period_started_at"].startswith("2026-10-04")
    control = manager.active["controls"]["media_player.bedroom"]
    assert control["original_value"] == 0.8
    assert control["restoration_pending"]
