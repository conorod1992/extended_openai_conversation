"""An observed manual adjustment permanently ends restoration ownership."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from tests.test_quiet_hours_restoration_regressions import _active
from tests.test_quiet_hours_runtime import _stateful_public_manager


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("restart", [False, True])
async def test_unchanged_target_releases_manual_adjustment_before_later_restore(
    hass, kind, restart
):
    manager = _stateful_public_manager(hass)
    manager._config = runtime.QuietHoursConfig(
        enabled=True, max_volume=0.2, wake_sound="off"
    )
    entity_id = "media_player.bedroom" if kind == "volume" else "switch.wake_sound"
    manager._active = _active(entity_id, kind)
    manager._active["controls"][entity_id]["ceiling_attempts"] = 3
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
    state = SimpleNamespace(
        state="idle" if kind == "volume" else "on",
        attributes={"volume_level": 0.4},
        context=SimpleNamespace(id="manual"),
    )
    hass.states.get.side_effect = lambda _: state
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    capability = runtime.SatelliteCapabilities(
        satellite_entity_id="assist_satellite.bedroom",
        name="Bedroom",
        device_id="bedroom",
        media_player_entity_id=entity_id if kind == "volume" else None,
        wake_sound_entity_id=entity_id if kind == "switch" else None,
        media_player_source=None,
        wake_sound_source=None,
    )
    await manager._async_reconcile_controls_locked([capability])
    assert entity_id not in manager.active["controls"]
    # The user subsequently chooses the quiet value again. It is still theirs.
    state.attributes["volume_level"] = 0.2
    state.state = "idle" if kind == "volume" else "off"
    await manager._async_restore_locked()
    manager._async_set_volume.assert_not_awaited()
    manager._async_set_switch.assert_not_awaited()


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("reconciled", [False, True])
async def test_shutdown_replacement_preserves_released_choice_in_same_period(
    hass, monkeypatch, kind, reconciled
):
    from datetime import UTC, datetime, timedelta
    from tests.test_quiet_hours_runtime import (
        _config,
        _install_registry,
        _install_services,
        _seed_room,
    )

    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.6)
    manager._config = _config()
    now = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(runtime.dt_util, "now", lambda: now)
    monkeypatch.setattr(runtime.QuietHoursManager, "_reschedule", lambda self: None)
    volume_calls, switch_calls = await _install_services(hass)
    persisted = {}

    async def save(data):
        persisted.clear()
        persisted.update(deepcopy(data))

    store = SimpleNamespace(
        async_save=AsyncMock(side_effect=save),
        async_load=AsyncMock(side_effect=lambda: deepcopy(persisted)),
    )
    manager._store = store
    await manager.async_reconcile(now=now)
    entity_id = (
        "media_player.bedroom" if kind == "volume" else "switch.bedroom_wake_sound"
    )
    if kind == "volume":
        hass.states.async_set(entity_id, "idle", {"volume_level": 0.4})
    else:
        hass.states.async_set(entity_id, "on")
    hass.states.get(entity_id).context = SimpleNamespace(id="independent")
    if reconciled:
        await manager.async_reconcile(now=now)
    await manager.async_shutdown()
    # Still-owned controls restore, independently chosen values do not.
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == (
        0.4 if kind == "volume" else 0.6
    )
    assert hass.states.get("switch.bedroom_wake_sound").state == "on"
    replacement = runtime.QuietHoursManager(hass)
    replacement._store = store
    await replacement.async_setup()
    assert entity_id not in replacement.active["controls"]
    assert entity_id in replacement.active["observed_controls"]
    assert hass.states.get(entity_id).attributes.get("volume_level") == (
        0.4 if kind == "volume" else None
    )
    assert hass.states.get("switch.bedroom_wake_sound").state == (
        "on" if kind == "switch" else "off"
    )
    before = (list(volume_calls), list(switch_calls))
    await manager._handle_discovery_tick(now)
    await manager._handle_time_transition(now)
    await replacement.async_update_config(_config().as_dict())
    await replacement.async_update_config(_config().as_dict())
    assert (volume_calls, switch_calls) == before
    # Released observations expire with the policy period, not with the manager.
    now += timedelta(days=1)
    await replacement.async_reconcile(now=now)
    assert entity_id in replacement.active["controls"]
    await replacement.async_shutdown()
