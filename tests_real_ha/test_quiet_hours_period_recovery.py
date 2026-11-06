"""Real HA state, service and disk boundaries for offline Quiet Hours controls."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
)
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)


@pytest.fixture
def real_store_io():
    """Select native Store files through the acceptance suite's I/O fixture."""
    return True


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("returned_quiet", [False, True])
async def test_offline_control_rejoins_later_period_with_durable_baseline(
    hass, monkeypatch, real_store_io, kind, restart, returned_quiet
):
    await hass.config.async_set_time_zone("UTC")
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    _, media, wake = _install_satellite_entities(hass, volume=0.8)
    calls = []
    _install_control_services(hass, calls)
    manager = QuietHoursManager(hass)
    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(enabled=True)
    store_path = Path(hass.config.path(".storage", runtime._STORAGE_KEY))
    entity = media if kind == "volume" else wake
    try:
        await manager.async_reconcile(now=now)
        original = 0.8 if kind == "volume" else True
        assert manager.active["controls"][entity]["original_value"] == original
        hass.states.async_set(entity, "unavailable")
        for day in (1, 2):
            now = datetime(2026, 11, 1 + day, 8, 30, tzinfo=UTC)
            await manager.async_reconcile(now=now)
            now = now.replace(hour=22)
            await manager.async_reconcile(now=now)
            disk = json.loads(store_path.read_text(encoding="utf-8"))["data"]
            assert disk["active"]["controls"][entity]["original_value"] == original
        if restart:
            await manager.async_shutdown()
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        calls.clear()
        now = now.replace(hour=23, minute=30)
        hass.states.async_set(
            entity,
            "idle" if kind == "volume" else "off" if returned_quiet else "on",
            {"volume_level": 0.2 if returned_quiet else 0.8}
            if kind == "volume"
            else {},
        )
        for _ in range(3):
            await manager.async_reconcile(now=now)
        if kind == "volume":
            assert hass.states.get(entity).attributes["volume_level"] == 0.2
        else:
            assert hass.states.get(entity).state == "off"
        assert calls == (
            []
            if returned_quiet
            else [("volume_set" if kind == "volume" else "turn_off", entity)]
        )
        disk = json.loads(store_path.read_text(encoding="utf-8"))["data"]
        assert disk["active"]["controls"][entity]["original_value"] == original
        assert not disk["active"]["controls"][entity].get("restore_before_apply")
        now = (now + timedelta(days=1)).replace(hour=8)
        await manager.async_reconcile(now=now)
        if kind == "volume":
            assert hass.states.get(entity).attributes["volume_level"] == 0.8
        else:
            assert hass.states.get(entity).state == "on"
        assert manager.active is None
    finally:
        await manager.async_shutdown()


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("restart", [False, True])
async def test_available_quiet_effect_carries_across_missed_daytime_boundary(
    hass, monkeypatch, real_store_io, deferred, restart
):
    """No daytime-volume command is valid while the same ceiling is required."""
    await hass.config.async_set_time_zone("UTC")
    now = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    _, media, _ = _install_satellite_entities(hass, volume=0.8)
    calls, pending = [], []

    def report(value, context):
        hass.states.async_set(media, "idle", {"volume_level": value}, context=context)

    async def volume_set(call):
        value = call.data["volume_level"]
        calls.append(value)
        if deferred:
            pending.append((value, call.context))
        else:
            report(value, call.context)

    def feedback():
        for value, context in pending:
            report(value, context)
        pending.clear()

    hass.services.async_register("media_player", "volume_set", volume_set)
    manager = QuietHoursManager(hass)
    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(enabled=True, wake_sound="unchanged")
    try:
        await manager.async_reconcile(now=now)
        feedback()
        assert calls == [0.2]
        calls.clear()
        now += timedelta(days=1)
        if restart:
            # Crash-style replacement: no shutdown restoration or daytime read.
            manager = QuietHoursManager(hass)
            await manager.async_setup()
        for _ in range(4):
            await manager.async_reconcile(now=now)
            feedback()
            assert calls == []
            assert hass.states.get(media).attributes["volume_level"] == 0.2
            assert manager.active["controls"][media]["original_value"] == 0.8
            now += timedelta(minutes=5)
        disk = json.loads(
            Path(hass.config.path(".storage", runtime._STORAGE_KEY)).read_text(
                encoding="utf-8"
            )
        )["data"]
        assert disk["active"]["controls"][media]["original_value"] == 0.8
        now = (now + timedelta(days=1)).replace(hour=8)
        await manager.async_reconcile(now=now)
        feedback()
        assert calls == [0.8]
        assert hass.states.get(media).attributes["volume_level"] == 0.8
        assert manager.active is None
    finally:
        await manager.async_shutdown()
