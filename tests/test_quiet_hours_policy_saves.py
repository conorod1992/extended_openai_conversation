"""Policy saves must not erase observations that protect a manual choice."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.test_quiet_hours_runtime import (
    _config,
    _install_registry,
    _install_services,
    _seed_room,
    _stateful_public_manager,
)
from tests.test_review_quiet_recovery import timers  # noqa: F401

pytestmark = pytest.mark.usefixtures("timers")


@pytest.mark.parametrize("kind", ["volume", "switch"])
@pytest.mark.parametrize("observed_before_save", [False, True])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("change", ["identical", "schedule", "other_control"])
async def test_repeated_policy_saves_preserve_manual_choice(
    hass, monkeypatch, kind, observed_before_save, restart, change
):
    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.8)
    manager._config = _config()
    volumes, switches = await _install_services(hass)
    await manager.async_reconcile()
    entity = "media_player.bedroom" if kind == "volume" else "switch.bedroom_wake_sound"
    if kind == "volume":
        hass.states.async_set(entity, "idle", {"volume_level": 0.4})
    else:
        hass.states.async_set(entity, "on")
    if observed_before_save:
        await manager.async_reconcile()
    volumes.clear()
    switches.clear()
    candidate = manager.config.as_dict()
    if change == "schedule":
        candidate["start"] = "21:00"
    elif change == "other_control":
        candidate["wake_sound" if kind == "volume" else "max_volume"] = (
            "on" if kind == "volume" else 0.1
        )
    for attempt in range(3):
        await manager.async_update_config(candidate)
        if restart and attempt == 0:
            saved = deepcopy(manager._store.async_save.call_args.args[0])
            manager = _stateful_public_manager(hass)
            manager._config = _config(**saved["config"])
            manager._active = manager._normalize_active(saved["active"])
        assert entity not in manager.active["controls"]
        assert entity in manager.active["observed_controls"]
        assert (
            hass.states.get(entity).attributes["volume_level"]
            if kind == "volume"
            else hass.states.get(entity).state
        ) == (0.4 if kind == "volume" else "on")
    await manager.async_set_enabled(False)
    assert not [
        call
        for call in (volumes if kind == "volume" else switches)
        if call[0] == entity
    ]


@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_noop_save_keeps_pending_baseline_until_manual_change_is_observed(
    hass, monkeypatch, kind
):
    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.4)
    manager._config = _config()
    entity = "media_player.bedroom" if kind == "volume" else "switch.bedroom_wake_sound"
    hass.states.get(entity).context = SimpleNamespace(id="manual")
    manager._active = {
        "period_started_at": "2026-09-11T22:00:00+00:00",
        "period_ends_at": "2026-09-12T07:00:00+00:00",
        "controls": {},
        "observed_controls": [entity],
        "pending_controls": {
            entity: {
                "kind": kind,
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": 0.8 if kind == "volume" else True,
                "quiet_value": 0.2 if kind == "volume" else False,
                "baseline_context_id": "original",
            }
        },
    }
    volumes, switches = await _install_services(hass)
    for _ in range(3):
        await manager.async_update_config(manager.config.as_dict())
        assert entity not in manager.active["controls"]
        assert entity not in manager.active["pending_controls"]
        assert entity in manager.active["observed_controls"]
    assert not [
        call
        for call in (volumes if kind == "volume" else switches)
        if call[0] == entity
    ]


@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_changed_control_target_reconsiders_previously_unchanged_value(
    hass, monkeypatch, kind
):
    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.1, wake="off")
    manager._config = _config()
    volumes, switches = await _install_services(hass)
    await manager.async_reconcile()
    assert not manager.active["controls"]
    candidate = manager.config.as_dict()
    candidate["max_volume" if kind == "volume" else "wake_sound"] = (
        0.05 if kind == "volume" else "on"
    )
    await manager.async_update_config(candidate)
    assert (volumes if kind == "volume" else switches) == [
        ("media_player.bedroom", 0.05)
        if kind == "volume"
        else ("switch.bedroom_wake_sound", True)
    ]
    await manager.async_set_enabled(False)
    assert (volumes if kind == "volume" else switches)[-1] == (
        ("media_player.bedroom", 0.1)
        if kind == "volume"
        else ("switch.bedroom_wake_sound", False)
    )


@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_explicit_reenable_starts_fresh_observations(hass, monkeypatch, kind):
    _install_registry(monkeypatch, "bedroom")
    manager = _stateful_public_manager(hass)
    _seed_room(hass, "bedroom", volume=0.8)
    manager._config = _config()
    volumes, switches = await _install_services(hass)
    await manager.async_reconcile()
    entity = "media_player.bedroom" if kind == "volume" else "switch.bedroom_wake_sound"
    hass.states.async_set(
        entity, "idle" if kind == "volume" else "on", {"volume_level": 0.4}
    )
    await manager.async_reconcile()
    await manager.async_set_enabled(False)
    assert manager.active is None
    volumes.clear()
    switches.clear()
    await manager.async_set_enabled(True)
    assert (volumes if kind == "volume" else switches) == [
        (entity, 0.2 if kind == "volume" else False)
    ]
