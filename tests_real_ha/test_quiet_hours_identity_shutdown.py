"""Actual registry observations and queued HA callbacks retain their ownership."""

import asyncio
from copy import deepcopy
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.extended_openai_conversation_responses.quiet_hours import (
    QuietHoursManager,
    async_get_quiet_hours,
)
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from tests_real_ha.test_quiet_hours_scheduling import (
    _install_control_services,
    _install_satellite_entities,
)


async def _enabled_manager(hass, volume):
    satellite, media, _ = _install_satellite_entities(hass, volume=volume)
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    now = dt_util.now()
    await manager.async_update_config(
        {
            "enabled": True,
            "start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "max_volume": 0.2,
            "wake_sound": "unchanged",
        }
    )
    return manager, satellite, media, calls


async def test_cancelled_shutdown_waits_for_lock_and_restores_before_removal(hass):
    from custom_components.extended_openai_conversation_responses import (
        async_remove_entry,
    )
    from tests_real_ha.test_live_subentry_removal import _entry

    manager, _, media, calls = await _enabled_manager(hass, 0.8)
    assert hass.states.get(media).attributes["volume_level"] == 0.2
    await manager._lock.acquire()
    stopped = asyncio.create_task(async_remove_entry(hass, _entry()))
    try:
        async with asyncio.timeout(10):
            while not manager._stopping:
                await asyncio.sleep(0)
        stopped.cancel()
        await asyncio.sleep(0)
        stopped.cancel()
        await asyncio.sleep(0)
        assert not stopped.done(), "Removal must retain ownership while shutdown waits"
    finally:
        manager._lock.release()
    with pytest.raises(asyncio.CancelledError):
        await stopped
    assert hass.states.get(media).attributes["volume_level"] == 0.8
    assert calls == [("volume_set", media), ("volume_set", media)]
    assert manager.active is None
    assert (await manager._store.async_load())["active"] is None
    assert not manager._unsubscribers
    assert manager._registry_unsubscribe is None
    assert hass.states.get(manager._state_entity_id()) is None
    assert (
        "quiet_hours_manager" not in hass.data["extended_openai_conversation_responses"]
    )


@pytest.mark.parametrize("original", [0.1, 0.8])
async def test_observation_identity_survives_rename_and_restart(hass, original):
    manager, _, media, calls = await _enabled_manager(hass, original)
    try:
        hass.states.async_set(
            media, "idle", {"volume_level": 0.7}, context=Context(user_id="manual")
        )
        await manager.async_reconcile()
        calls.clear()
        registry = er.async_get(hass)
        entry = registry.async_get(media)
        renamed = "media_player.renamed_observation"
        registry.async_update_entity(media, new_entity_id=renamed)
        state = hass.states.get(media)
        hass.states.async_remove(media)
        hass.states.async_set(
            renamed, state.state, dict(state.attributes), context=state.context
        )
        await manager.async_reconcile()
        assert not calls
        assert hass.states.get(renamed).attributes["volume_level"] == 0.7
        stored = await manager._store.async_load()
        assert renamed in stored["active"]["observed_controls"]
        assert media not in stored["active"]["observed_controls"]
        assert stored["active"]["observed_identities"][renamed] == [
            entry.domain,
            entry.platform,
            entry.unique_id,
        ]
        # Recreate the manager without a graceful restoration that could hide
        # a lost observation by constructing a new baseline.
        manager._cancel_scheduled_callbacks()
        from custom_components.extended_openai_conversation_responses.quiet_hours import (
            QuietHoursManager,
        )

        restarted = QuietHoursManager(hass)
        await restarted.async_setup()
        try:
            assert not calls
            assert hass.states.get(renamed).attributes["volume_level"] == 0.7
            await restarted.async_set_enabled(False)
            assert not calls
        finally:
            await restarted.async_shutdown()
    finally:
        await manager.async_shutdown()


async def test_deleted_observation_does_not_suppress_reused_id(hass):
    manager, _, media, calls = await _enabled_manager(hass, 0.1)
    try:
        registry = er.async_get(hass)
        entry = registry.async_get(media)
        registry.async_remove(media)
        hass.states.async_remove(media)
        replacement = registry.async_get_or_create(
            entry.domain,
            entry.platform,
            "replacement-speaker",
            suggested_object_id=media.split(".")[1],
            device_id=entry.device_id,
        )
        assert replacement.entity_id == media
        hass.states.async_set(media, "idle", {"volume_level": 0.8})
        await manager.async_reconcile()
        assert calls == [("volume_set", media)]
        assert manager.active["controls"][media]["original_value"] == 0.8
        await manager.async_set_enabled(False)
        assert hass.states.get(media).attributes["volume_level"] == 0.8
    finally:
        await manager.async_shutdown()


async def test_registered_callback_queued_behind_shutdown_cannot_reapply(
    hass, monkeypatch
):
    manager, _, media, calls = await _enabled_manager(hass, 0.7)
    save = manager._store.async_save
    entered, finish = asyncio.Event(), asyncio.Event()
    writes = []

    async def blocked_save(data):
        writes.append(deepcopy(data))
        entered.set()
        await finish.wait()
        await save(data)

    monkeypatch.setattr(manager._store, "async_save", blocked_save)
    await manager._lock.acquire()
    stopped = asyncio.create_task(manager.async_shutdown())
    await asyncio.sleep(0)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await asyncio.sleep(0)
    manager._lock.release()
    try:
        await entered.wait()
        finish.set()
        await stopped
        await hass.async_block_till_done(wait_background_tasks=True)
        assert calls == [("volume_set", media), ("volume_set", media)]
        assert hass.states.get(media).attributes["volume_level"] == 0.7
        assert writes[-1]["active"] is None
        assert (await manager._store.async_load())["active"] is None
        assert manager.active is None
        assert not manager._unsubscribers
        assert hass.states.get(manager._state_entity_id()) is None
        await manager.async_setup()
        await manager.async_reconcile()
        assert hass.states.get(media).attributes["volume_level"] == 0.7
        assert len(calls) == 2
    finally:
        finish.set()
        await stopped


@pytest.mark.parametrize("setup_timing", ["queued", "after_shutdown"])
async def test_public_getter_after_shutdown_cannot_reattach_registry_listener(
    hass, monkeypatch, setup_timing
):
    registry_events = []
    original_callback = QuietHoursManager._async_registry_updated

    async def observed_callback(self, event):
        registry_events.append(event)
        await original_callback(self, event)

    monkeypatch.setattr(QuietHoursManager, "_async_registry_updated", observed_callback)
    manager, _, media, calls = await _enabled_manager(hass, 0.8)
    await hass.async_block_till_done()
    state_id = manager._state_entity_id()
    listeners_before = hass.bus.async_listeners()[er.EVENT_ENTITY_REGISTRY_UPDATED]
    assert manager._registry_unsubscribe is not None
    assert manager._unsubscribers
    entered, finish = asyncio.Event(), asyncio.Event()
    stopped = setup = None
    try:
        if setup_timing == "queued":
            save = manager._store.async_save

            async def blocked_save(data):
                entered.set()
                await finish.wait()
                await save(data)

            monkeypatch.setattr(manager._store, "async_save", blocked_save)
            stopped = asyncio.create_task(manager.async_shutdown())
            async with asyncio.timeout(10):
                await entered.wait()
            assert manager._stopping
            assert manager._lock.locked()
            setup = asyncio.create_task(async_get_quiet_hours(hass))
            await asyncio.sleep(0)
            assert not setup.done()
            finish.set()
            await stopped
            assert await setup is manager
        else:
            await manager.async_shutdown()
            assert await async_get_quiet_hours(hass) is manager

        await hass.async_block_till_done()
        registry_events.clear()
        assert manager._registry_unsubscribe is None
        assert (
            hass.bus.async_listeners().get(er.EVENT_ENTITY_REGISTRY_UPDATED, 0)
            == listeners_before - 1
        )
        assert not manager._unsubscribers
        assert manager.active is None
        assert hass.states.get(state_id) is None
        assert hass.states.get(media).attributes["volume_level"] == 0.8
        assert calls == [("volume_set", media), ("volume_set", media)]

        er.async_get(hass).async_update_entity(state_id, name="Stopped Quiet Hours")
        await hass.async_block_till_done()
        assert not registry_events
        assert not manager._unsubscribers
        assert hass.states.get(state_id) is None
        assert len(calls) == 2
    finally:
        finish.set()
        if stopped is not None:
            await stopped
        if setup is not None:
            await setup
        await manager.async_shutdown()


async def _manual_manager(hass, *, enabled=True):
    satellite, automatic_media, automatic_wake = _install_satellite_entities(hass)
    registry = er.async_get(hass)
    media = registry.async_get_or_create(
        "media_player", "test", "manual-speaker", suggested_object_id="chosen_speaker"
    ).entity_id
    wake = registry.async_get_or_create(
        "switch", "test", "manual-wake", suggested_object_id="chosen_wake"
    ).entity_id
    hass.states.async_set(media, "idle", {"volume_level": 0.8})
    hass.states.async_set(wake, "on")
    calls = []
    _install_control_services(hass, calls)
    manager = await async_get_quiet_hours(hass)
    now = dt_util.now()
    await manager.async_update_config(
        {
            "enabled": enabled,
            "start": (now - timedelta(hours=1)).strftime("%H:%M"),
            "end": (now + timedelta(hours=1)).strftime("%H:%M"),
            "max_volume": 0.2,
            "wake_sound": "off",
            "overrides": {
                satellite: {
                    "media_player_entity_id": media,
                    "wake_sound_entity_id": wake,
                }
            },
        }
    )
    return manager, satellite, media, wake, automatic_media, automatic_wake, calls


def _rename_selected(hass, old_id, new_id):
    registry = er.async_get(hass)
    entry = registry.async_get(old_id)
    registry.async_update_entity(old_id, new_entity_id=new_id)
    state = hass.states.get(old_id)
    hass.states.async_remove(old_id)
    hass.states.async_set(
        new_id, state.state, dict(state.attributes), context=state.context
    )
    return [entry.domain, entry.platform, entry.unique_id]


def _retire_for_restart(manager):
    # Model a process replacement without the graceful end-of-period restore.
    manager._stopping = True
    manager._cancel_scheduled_callbacks()
    if manager._registry_unsubscribe is not None:
        manager._registry_unsubscribe()
        manager._registry_unsubscribe = None


@pytest.mark.parametrize("enabled", [True, False])
async def test_manual_selections_follow_registry_renames_and_restart(hass, enabled):
    (
        manager,
        satellite,
        media,
        wake,
        auto_media,
        auto_wake,
        calls,
    ) = await _manual_manager(hass, enabled=enabled)
    new_satellite, new_media, new_wake = (
        "assist_satellite.renamed",
        "media_player.renamed",
        "switch.renamed",
    )
    bindings = {
        new_media: _rename_selected(hass, media, new_media),
        new_wake: _rename_selected(hass, wake, new_wake),
        new_satellite: _rename_selected(hass, satellite, new_satellite),
    }
    calls.clear()
    await manager.async_reconcile()
    await hass.async_block_till_done()
    assert not calls, (
        calls,
        manager._config,
        manager._selection_identities,
        manager.active,
    )
    stored = await manager._store.async_load()
    assert stored["config"]["overrides"] == {
        new_satellite: {
            "media_player_entity_id": new_media,
            "wake_sound_entity_id": new_wake,
        }
    }
    assert stored["selection_identities"] == bindings
    _retire_for_restart(manager)
    restarted = QuietHoursManager(hass)
    await restarted.async_setup()
    try:
        assert not calls
        if not enabled:
            await restarted.async_set_enabled(True)
            assert sorted(calls) == sorted(
                [("volume_set", new_media), ("turn_off", new_wake)]
            )
        assert hass.states.get(new_media).attributes["volume_level"] == 0.2
        assert hass.states.get(new_wake).state == "off"
        assert hass.states.get(auto_media).attributes["volume_level"] == 0.6
        assert hass.states.get(auto_wake).state == "on"
        calls.clear()
        await restarted.async_update_config(restarted._config.as_dict())
        assert not calls
        await restarted.async_set_enabled(False)
        assert sorted(calls) == sorted(
            [("volume_set", new_media), ("turn_on", new_wake)]
        )
        assert hass.states.get(new_media).attributes["volume_level"] == 0.8
        assert hass.states.get(new_wake).state == "on"
    finally:
        await restarted.async_shutdown()


async def test_manual_selection_rename_preserves_independent_choice(hass):
    manager, satellite, media, wake, _, _, calls = await _manual_manager(hass)
    try:
        hass.states.async_set(wake, "on", context=Context(user_id="independent"))
        await manager.async_reconcile()
        renamed = "switch.released_choice"
        _rename_selected(hass, wake, renamed)
        calls.clear()
        await manager.async_reconcile()
        await manager.async_update_config(manager._config.as_dict())
        assert not calls
        assert hass.states.get(renamed).state == "on"
        assert renamed not in manager.active["controls"]
        await manager.async_set_enabled(False)
        assert calls == [("volume_set", media)]
    finally:
        await manager.async_shutdown()


async def test_deleted_manual_selection_does_not_control_reused_id(hass):
    manager, _, media, wake, _, _, calls = await _manual_manager(hass)
    try:
        registry = er.async_get(hass)
        registry.async_remove(wake)
        hass.states.async_remove(wake)
        replacement = registry.async_get_or_create(
            "switch", "test", "different-wake", suggested_object_id=wake.split(".")[1]
        )
        assert replacement.entity_id == wake
        hass.states.async_set(wake, "on")
        calls.clear()
        await manager.async_reconcile()
        await manager.async_update_config(manager._config.as_dict())
        assert not calls
        assert hass.states.get(wake).state == "on"
        await manager.async_set_enabled(False)
        assert calls == [("volume_set", media)]
    finally:
        await manager.async_shutdown()


async def test_selection_rename_save_failure_prevents_device_writes(hass, monkeypatch):
    manager, _, media, _, _, _, calls = await _manual_manager(hass)
    try:
        renamed = "media_player.renamed_during_save_failure"
        await manager._lock.acquire()
        _rename_selected(hass, media, renamed)
        original = manager._store.async_save

        async def fail(_data):
            raise OSError("save unavailable")

        monkeypatch.setattr(manager._store, "async_save", fail)
        calls.clear()
        manager._lock.release()
        with pytest.raises(OSError):
            await manager.async_reconcile()
        assert not calls
        assert hass.states.get(renamed).attributes["volume_level"] == 0.2
        monkeypatch.setattr(manager._store, "async_save", original)
        await manager.async_reconcile()
        assert not calls
        await manager.async_set_enabled(False)
        assert hass.states.get(renamed).attributes["volume_level"] == 0.8
    finally:
        monkeypatch.setattr(manager._store, "async_save", original)
        await manager.async_shutdown()
