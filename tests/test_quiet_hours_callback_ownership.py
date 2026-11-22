"""Scheduled Quiet Hours work follows the currently armed policy and timezone."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from tests.test_quiet_hours_runtime import _config, _runtime_manager

QUIET = datetime(2026, 11, 22, 22, 5, tzinfo=UTC)
AFTER_QUIET = datetime(2026, 11, 22, 23, 31, tzinfo=UTC)


@pytest.fixture
def schedule(monkeypatch):
    """Capture actual registered callbacks without simulating HA timer internals."""
    manager = _runtime_manager()
    manager.hass.config = SimpleNamespace(time_zone="UTC")
    manager._config = _config(start="21:00", end="23:30")
    manager._publish_state = Mock()
    manager._discover_capabilities = Mock(return_value=[])
    manager._async_restore_locked = AsyncMock()
    now = Mock(return_value=QUIET)
    monkeypatch.setattr(runtime.dt_util, "now", now)
    monkeypatch.setattr(runtime.dt_util, "utcnow", now)
    monkeypatch.setattr(runtime.dt_util, "as_local", lambda value: value)
    calls = []

    def track(_hass, callback, *_args, **_kwargs):
        calls.append(callback)
        return Mock()

    monkeypatch.setattr(runtime, "async_track_time_change", track)
    monkeypatch.setattr(runtime, "async_track_time_interval", track)
    monkeypatch.setattr(runtime, "async_track_point_in_utc_time", track)
    manager._reschedule()
    return manager, now, calls


@pytest.mark.parametrize("save_fails", [False, True])
async def test_policy_save_retires_blocked_callback_only_on_replacement(
    schedule, save_fails
):
    manager, now, callbacks = schedule
    old_callback = callbacks[0]
    pending, release = asyncio.Event(), asyncio.Event()
    prior_policy = manager.config.as_dict()

    async def save(data):
        if data["config"]["start"] == "22:00" and not pending.is_set():
            pending.set()
            await release.wait()
            if save_fails:
                raise OSError("settings not saved")

    manager._store.async_save.side_effect = save
    manager._store.async_load.return_value = {"config": prior_policy, "active": None}
    saving = asyncio.create_task(
        manager.async_update_config({**prior_policy, "start": "22:00"})
    )
    await pending.wait()
    callback = asyncio.create_task(old_callback(QUIET))
    await asyncio.sleep(0)
    assert not callback.done()
    if not save_fails:
        now.return_value = AFTER_QUIET
    release.set()
    await callback

    if save_fails:
        with pytest.raises(OSError, match="settings not saved"):
            await saving
        assert manager.config.as_dict() == prior_policy
        assert len(callbacks) == 3
        assert manager._publish_state.call_count == 1
        assert manager._publish_state.call_args.args[0] is not None
    else:
        await saving
        assert len(callbacks) == 6
        manager._publish_state.assert_called_once_with(None)


async def test_current_callback_samples_time_after_waiting_on_lock(schedule):
    manager, now, callbacks = schedule
    async with manager._lock:
        callback = asyncio.create_task(callbacks[-1](QUIET))
        await asyncio.sleep(0)
        assert not callback.done()
        now.return_value = AFTER_QUIET
    await callback
    manager._publish_state.assert_called_once_with(None)


async def test_timezone_change_while_preparing_fences_device_effect(schedule):
    manager, _now, callbacks = schedule
    entity_id = "media_player.bedroom"
    satellite_id = "assist_satellite.bedroom"
    manager.hass.states.async_set(entity_id, "idle", {"volume_level": 0.6})
    manager._discover_capabilities.return_value = [
        runtime.SatelliteCapabilities(
            satellite_id, "Bedroom", None, entity_id, None, None, None
        )
    ]
    preparing, release = asyncio.Event(), asyncio.Event()
    prepared = {
        "kind": "volume",
        "original_value": 0.6,
        "application_state": "prepared",
    }

    async def prepare(_satellite, entity, controls, *_args):
        controls[entity] = prepared
        await manager._async_save_locked()
        preparing.set()
        await release.wait()
        return prepared

    manager._async_prepare_control_locked = prepare
    manager._async_revalidate_control_locked = AsyncMock(return_value=True)
    callback = asyncio.create_task(callbacks[0](QUIET))
    await preparing.wait()
    manager.hass.config.time_zone = "Europe/Paris"
    release.set()
    await callback

    manager.hass.services.async_call.assert_not_awaited()
    assert manager._active["controls"][entity_id] == prepared
    assert manager._persisted_active["controls"][entity_id] == prepared
    assert prepared["application_state"] == "prepared"


async def test_only_current_dst_callback_rearms_the_schedule(schedule, monkeypatch):
    manager, _now, callbacks = schedule
    monkeypatch.setattr(
        runtime, "_next_discontinuous_transition", lambda *_a, **_kw: QUIET
    )
    manager._reschedule()
    old_dst = callbacks[-2]
    manager._reschedule()
    current_dst = callbacks[-2]
    count = len(callbacks)
    await old_dst(QUIET)
    assert len(callbacks) == count
    await current_dst(QUIET)
    assert len(callbacks) == count + 4


async def test_recovery_tick_finishes_under_the_reloaded_schedule(schedule):
    manager, _now, callbacks = schedule
    manager._initialized = False
    manager._config_recovery_pending = True
    manager._store.async_load.return_value = {
        "config": manager.config.as_dict(),
        "active": None,
    }
    manager._reschedule()
    recovery_tick = callbacks[-1]
    count = len(callbacks)

    await recovery_tick(QUIET)

    assert manager._initialized
    assert not manager._config_recovery_pending
    assert len(callbacks) == count + 3
    assert manager._publish_state.call_count == 1
    assert manager._publish_state.call_args.args[0] is not None
    await recovery_tick(QUIET)
    assert manager._publish_state.call_count == 1
