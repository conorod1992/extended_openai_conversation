"""Quiet Hours recovers devices and identity ownership, not just configuration."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours as qh,
    quiet_hours_runtime as runtime,
)
from tests.test_quiet_hours_runtime import (
    _config,
    _install_registry,
    _install_services,
    _seed_room,
    _stateful_public_manager,
)


@pytest.fixture
def timers(monkeypatch):
    clock, interval = (
        Mock(side_effect=lambda *a, **k: Mock()),
        Mock(side_effect=lambda *a, **k: Mock()),
    )
    monkeypatch.setattr(runtime, "async_track_time_change", clock)
    monkeypatch.setattr(runtime, "async_track_time_interval", interval)
    monkeypatch.setattr(
        runtime.dt_util, "now", lambda: datetime(2026, 9, 11, 23, tzinfo=UTC)
    )
    return clock, interval


async def _manager(hass, monkeypatch, enabled):
    _install_registry(monkeypatch, "bedroom")
    _seed_room(hass, "bedroom", volume=0.8)
    manager = _stateful_public_manager(hass)
    manager._config = _config(enabled=enabled)
    await _install_services(hass)
    await manager.async_reconcile()
    return manager


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("change", ["enable", "disable", "schedule"])
async def test_ambiguous_config_write_reconciles_devices_and_survives_restart(
    hass, monkeypatch, timers, committed, change
):
    manager = await _manager(hass, monkeypatch, change != "enable")
    original = deepcopy(manager.config.as_dict())
    candidate = _config(
        enabled=change != "disable",
        start="21:00" if change == "schedule" else "22:00",
        max_volume=0.1 if change == "schedule" else 0.2,
    ).as_dict()
    persisted = {"config": original, "active": deepcopy(manager._active)}
    first = True

    async def save(data):
        nonlocal first, persisted
        if first:
            first = False
            if committed:
                persisted = deepcopy(data)
            raise OSError("configuration acknowledgement failed")
        persisted = deepcopy(data)

    async def load():
        return deepcopy(persisted)

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    with pytest.raises(OSError, match="acknowledgement"):
        await asyncio.wait_for(manager.async_update_config(candidate), 2)
    expected = candidate if committed else original
    assert manager.config.as_dict() == expected
    quiet = expected["enabled"]
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == (
        expected["max_volume"] if quiet else 0.8
    )
    assert hass.states.get("switch.bedroom_wake_sound").state == (
        "off" if quiet else "on"
    )
    if committed and not quiet:
        assert manager.active is None
        assert manager._unsubscribers == []
    restarted = _stateful_public_manager(hass)
    restarted._initialized = False
    restarted._store = manager._store
    await restarted.async_setup()
    assert restarted.config == manager.config
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == (
        expected["max_volume"] if quiet else 0.8
    )
    if quiet:
        assert (
            restarted.active["controls"]["media_player.bedroom"]["original_value"]
            == 0.8
        )


async def test_failed_readback_suspends_device_changes_and_keeps_recovery_timer(
    hass, monkeypatch, timers
):
    manager = await _manager(hass, monkeypatch, True)
    persisted = {
        "config": manager.config.as_dict(),
        "active": deepcopy(manager._active),
    }
    unreadable = True

    async def save(data):
        persisted.update(deepcopy(data))
        if unreadable:
            raise OSError("write ack failed")

    async def load():
        if unreadable:
            raise OSError("read unavailable")
        return deepcopy(persisted)

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    with pytest.raises(OSError, match="write ack"):
        await manager.async_update_config(_config(enabled=False).as_dict())
    assert manager._config_recovery_pending
    assert len(manager._unsubscribers) == 1
    hass.services.async_call.reset_mock()
    await manager.async_reconcile()
    hass.services.async_call.assert_not_awaited()
    unreadable = False
    await manager.async_reconcile()
    assert not manager._config_recovery_pending
    assert not manager.config.enabled
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8
    assert manager.active is None
    assert manager._unsubscribers == []


async def test_restore_save_failure_keeps_retry_without_repeating_manual_changes(
    hass, monkeypatch, timers
):
    manager = await _manager(hass, monkeypatch, True)
    persisted = {
        "config": manager.config.as_dict(),
        "active": deepcopy(manager._active),
    }
    writes = 0

    async def save(data):
        nonlocal writes
        writes += 1
        if writes == 1:
            persisted.update(deepcopy(data))
        raise OSError("storage failure")

    manager._store = SimpleNamespace(
        async_save=save, async_load=AsyncMock(side_effect=lambda: deepcopy(persisted))
    )
    with pytest.raises(OSError, match="storage failure"):
        await manager.async_update_config(_config(enabled=False).as_dict())
    # The first recovery restored devices but could not clear durable ownership.
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8
    hass.states.async_set("media_player.bedroom", "idle", {"volume_level": 0.4})
    restarted = _stateful_public_manager(hass)
    restarted._initialized = False

    async def successful_save(data):
        persisted.update(deepcopy(data))

    restarted._store = SimpleNamespace(
        async_load=AsyncMock(side_effect=lambda: deepcopy(persisted)),
        async_save=successful_save,
    )
    await restarted.async_setup()
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.4
    assert restarted.active is None


async def test_cancelled_config_write_is_settled_before_readback_and_restoration(
    hass, monkeypatch, timers
):
    manager = await _manager(hass, monkeypatch, True)
    entered, finish = asyncio.Event(), asyncio.Event()
    persisted = {
        "config": manager.config.as_dict(),
        "active": deepcopy(manager._active),
    }

    async def save(data):
        entered.set()
        await finish.wait()
        persisted.update(deepcopy(data))

    manager._store = SimpleNamespace(
        async_save=save, async_load=AsyncMock(side_effect=lambda: deepcopy(persisted))
    )
    task = asyncio.create_task(
        manager.async_update_config(_config(enabled=False).as_dict())
    )
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager.config.enabled
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8
    assert manager.active is None


def _control(identity, baseline):
    return {
        "kind": "volume",
        "registry_identity": ["media_player", "example", identity],
        "original_value": baseline,
        "quiet_value": 0.2,
        "application_state": "applied",
    }


@pytest.mark.parametrize("mixed", [False, True])
def test_rename_swaps_are_resolved_across_owned_and_pending_maps(
    hass, monkeypatch, mixed
):
    manager = _stateful_public_manager(hass)
    lookup = {"A": "media_player.new", "B": "media_player.old"}
    monkeypatch.setattr(
        qh.er,
        "async_get",
        lambda h: SimpleNamespace(async_get_entity_id=lambda d, p, u: lookup.get(u)),
    )
    second = "pending_controls" if mixed else "controls"
    manager._active = {
        "controls": {"media_player.old": _control("A", 0.8)},
        "pending_controls": {},
        "observed_controls": ["media_player.old", "media_player.new"],
    }
    manager._active[second]["media_player.new"] = _control("B", 0.6)
    assert manager._migrate_control_entity_ids()
    assert (
        manager._active["controls"]["media_player.new"]["registry_identity"][2] == "A"
    )
    assert manager._active[second]["media_player.old"]["registry_identity"][2] == "B"
    assert not manager._migrate_control_entity_ids()


@pytest.mark.parametrize("swap", [False, True])
async def test_rename_chain_restores_current_identities_and_never_reused_old_id(
    hass, monkeypatch, swap
):
    manager = _stateful_public_manager(hass)
    destination = "media_player.old" if swap else "media_player.other"
    lookup = {"A": "media_player.new", "B": destination}
    monkeypatch.setattr(
        qh.er,
        "async_get",
        lambda h: SimpleNamespace(async_get_entity_id=lambda d, p, u: lookup.get(u)),
    )
    for name in ("old", "new", "other"):
        hass.states.async_set(f"media_player.{name}", "idle", {"volume_level": 0.2})
    calls, _ = await _install_services(hass)
    manager._active = {
        "controls": {
            "media_player.old": _control("A", 0.8),
            "media_player.new": _control("B", 0.6),
        },
        "observed_controls": ["media_player.old", "media_player.new"],
    }
    await manager._async_restore_locked()
    assert calls == [("media_player.new", 0.8), (destination, 0.6)]
    assert hass.states.get("media_player.old").attributes["volume_level"] == (
        0.6 if swap else 0.2
    )
    # Store doubles keep references; capture migration persistence before actions
    # separately in the collision/restart test below.
    assert manager._store.async_save.await_count >= 2


async def test_destination_collision_preserves_canonical_baseline_across_restart(
    hass, monkeypatch
):
    manager = _stateful_public_manager(hass)
    monkeypatch.setattr(
        qh.er,
        "async_get",
        lambda h: SimpleNamespace(
            async_get_entity_id=lambda *identity: "media_player.new"
        ),
    )
    for name in ("old", "new"):
        hass.states.async_set(f"media_player.{name}", "idle", {"volume_level": 0.2})
    calls, _ = await _install_services(hass)
    manager._active = {
        "period_started_at": "2026-09-11T22:00:00+00:00",
        "period_ends_at": "2026-09-12T07:00:00+00:00",
        "controls": {
            "media_player.old": _control("A", 0.8),
            "media_player.new": _control("A", 0.6),
        },
        "observed_controls": ["media_player.old", "media_player.new"],
    }
    generations = []
    manager._store.async_save.side_effect = lambda data: generations.append(
        deepcopy(data)
    )
    await manager._async_restore_locked()
    assert calls == [("media_player.new", 0.6)]
    migrated = generations[0]["active"]
    assert set(migrated["controls"]) == {"media_player.new"}
    assert migrated["observed_controls"] == ["media_player.new"]
    restarted = _stateful_public_manager(hass)
    restarted._active = restarted._normalize_active(migrated)
    calls.clear()
    await restarted._async_restore_locked()
    assert calls == []


def test_conflicting_unanchored_ownership_is_released_without_reclaim(
    hass, monkeypatch
):
    manager = _stateful_public_manager(hass)
    monkeypatch.setattr(
        qh.er,
        "async_get",
        lambda h: SimpleNamespace(
            async_get_entity_id=lambda *identity: "media_player.current"
        ),
    )
    manager._active = {
        "controls": {"media_player.first": _control("A", 0.8)},
        "pending_controls": {"media_player.second": _control("A", 0.6)},
    }
    assert manager._migrate_control_entity_ids()
    assert manager._active["controls"] == {}
    assert manager._active["pending_controls"] == {}
    assert manager._active["observed_controls"] == ["media_player.current"]
