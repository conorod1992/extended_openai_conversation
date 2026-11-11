"""Unverified saved obligations are preserved without running Quiet Hours."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours as public,
    quiet_hours_runtime as runtime,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_quiet_hours_discovery import (
    END,
    MEDIA,
    MID,
    NEXT,
    SAT,
    START,
    WAKE,
    setup_controls,  # noqa: F401
)


def _record(entity=MEDIA, *, verified=False):
    return {
        "kind": "volume" if entity == MEDIA else "switch",
        "satellite_entity_id": SAT,
        "original_value": 0.8 if entity == MEDIA else True,
        "quiet_value": 0.2 if entity == MEDIA else False,
        "application_state": "applied",
        "legacy_metadata": {"must_survive": [1, 2, 3]},
        **(
            {"registry_identity": [entity.partition(".")[0], "esphome", entity]}
            if verified
            else {}
        ),
    }


def _generation(layout="controls", *, enabled=True):
    active = {
        "period_started_at": START.isoformat(),
        "period_ends_at": END.isoformat(),
        "controls": {},
        "observed_controls": [MEDIA, WAKE],
        "unrecognised_legacy_field": {"baseline_evidence": "preserve"},
    }
    if layout == "targets":
        active.pop("controls")
        active["targets"] = {MEDIA: {"original_volume": 0.8, "quiet_volume": 0.2}}
    elif layout == "mixed":
        active["controls"] = {MEDIA: _record(verified=True), WAKE: _record(WAKE)}
    elif layout == "shadowed_pending":
        active["controls"] = {MEDIA: _record(verified=True)}
        active["pending_controls"] = {MEDIA: _record()}
    elif layout == "shadowed_targets":
        active["controls"] = {MEDIA: _record(verified=True)}
        active["targets"] = {MEDIA: {"original_volume": 0.7, "quiet_volume": 0.2}}
    else:
        active[layout] = {MEDIA: _record()}
    return {
        "config": runtime._config_from_data(
            {
                "enabled": enabled,
                "overrides": {
                    SAT: {"media_player_entity_id": MEDIA, "wake_sound_entity_id": WAKE}
                },
            }
        ).as_dict(),
        "selection_identities": {MEDIA: ["media_player", "esphome", MEDIA]},
        "active": active,
        "legacy_store_metadata": {"also": "preserve"},
    }


def _loading_manager(s, saved, manager_type=public.QuietHoursManager):
    manager = manager_type(s.hass)
    manager._store = SimpleNamespace(
        async_load=AsyncMock(side_effect=lambda: deepcopy(saved)),
        async_save=AsyncMock(),
    )
    return manager


@pytest.mark.parametrize(
    "manager_type", [runtime.QuietHoursManager, public.QuietHoursManager]
)
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "layout",
    [
        "controls",
        "pending_controls",
        "targets",
        "mixed",
        "shadowed_pending",
        "shadowed_targets",
    ],
)
async def test_legacy_store_is_unchanged_across_all_public_lifecycle_paths(
    setup_controls,
    manager_type,
    enabled,
    layout,
):
    s = setup_controls
    saved = _generation(layout, enabled=enabled)
    before = deepcopy(saved)
    # Even a matching live value and a present replacement ID prove no ownership.
    s.entries[MEDIA].unique_id = "replacement-device"
    s.report(MEDIA, 0.2)
    s.report(WAKE, "off")
    manager = _loading_manager(s, saved, manager_type)
    manager._normalize_active = Mock(
        side_effect=AssertionError("must check raw obligations first")
    )
    await manager.async_setup()
    assert manager.startup_unavailable
    assert manager.snapshot()["available"] is False
    assert "Administrator review" in manager.snapshot()["error"]
    assert "preserved" in manager.snapshot()["error"]
    assert manager._unsubscribers == []
    manager._normalize_active.assert_not_called()
    for now in (MID, END, NEXT):
        await manager.async_reconcile(now=now)
        await manager._handle_discovery_tick(now)
        await manager._handle_time_transition(now)
    await manager._handle_core_config_update(
        SimpleNamespace(data={"time_zone": "Europe/Dublin"})
    )
    await manager._async_restore_locked()
    await manager._async_restore_locked(pending_only=True)
    await manager.async_setup()
    manager._store.async_load.assert_awaited_once()
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager.async_set_enabled(not enabled)
    candidate = deepcopy(saved["config"])
    candidate["overrides"] = {
        SAT: {"media_player_entity_id": "media_player.replacement"}
    }
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager.async_update_config(candidate)
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager._async_save_locked()
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager._async_set_volume(MEDIA, 0.8)
    with pytest.raises(HomeAssistantError, match="Administrator review"):
        await manager._async_set_switch(WAKE, True)
    await manager.async_shutdown()
    manager._store.async_save.assert_not_awaited()
    s.hass.services.async_call.assert_not_awaited()
    assert saved == before
    replacement = _loading_manager(s, saved, manager_type)
    await replacement.async_setup()
    await replacement.async_shutdown()
    replacement._store.async_save.assert_not_awaited()
    assert replacement.startup_unavailable
    assert saved == before


@pytest.mark.parametrize(
    "identity",
    [
        None,
        [],
        ["media_player", "esphome"],
        ["media_player", "esphome", ""],
        ["switch", "esphome", MEDIA],
        ["media_player", 1, MEDIA],
    ],
)
async def test_incomplete_or_invalid_identity_cannot_be_repaired_from_current_registry(
    setup_controls, identity
):
    s = setup_controls
    saved = _generation()
    saved["active"]["controls"][MEDIA]["registry_identity"] = identity
    before = deepcopy(saved)
    manager = _loading_manager(s, saved)
    await manager.async_setup()
    assert manager.startup_unavailable
    assert saved == before
    manager._store.async_save.assert_not_awaited()
    s.hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("layout", ["controls", "pending_controls", "targets", "mixed"])
async def test_recovery_readback_pauses_and_cancels_old_callbacks_without_installing_legacy_generation(
    setup_controls, layout
):
    s = setup_controls
    manager = s.manager
    previous_config = manager.config
    saved = _generation(layout)
    before = deepcopy(saved)
    manager._store = _loading_manager(s, saved)._store
    manager._config_recovery_pending = True
    unsubscribe = Mock()
    manager._unsubscribers = [unsubscribe]
    s.hass.states.async_set("binary_sensor.extended_openai_quiet_hours", "on")
    await manager.async_reconcile(now=MID)
    assert manager.startup_unavailable
    assert manager.config == previous_config
    assert manager.active is None
    assert manager._unsubscribers == []
    unsubscribe.assert_called_once()
    assert s.hass.states.get("binary_sensor.extended_openai_quiet_hours") is None
    # A later successful read cannot silently reopen this process after review
    # became necessary. Recovery requires an administrator and a fresh startup.
    saved["active"] = None
    manager._store.async_load.reset_mock()
    await manager.async_setup()
    await manager.async_reconcile(now=NEXT)
    manager._store.async_load.assert_not_awaited()
    manager._store.async_save.assert_not_awaited()
    s.hass.services.async_call.assert_not_awaited()
    saved["active"] = before["active"]
    assert saved == before
    await manager.async_shutdown()
    manager._store.async_save.assert_not_awaited()


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_save_readback_of_legacy_state_preserves_store_and_never_restores(
    setup_controls, cancel
):
    s = setup_controls
    await s.manager.async_reconcile(now=START)
    s.hass.services.async_call.reset_mock()
    saved = _generation("mixed")
    before = deepcopy(saved)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def load():
        entered.set()
        await finish.wait()
        return deepcopy(saved)

    store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("write failed before commit")),
        async_load=AsyncMock(side_effect=load),
    )
    s.manager._store = store
    candidate = s.manager.config.as_dict()
    candidate["enabled"] = False
    task = asyncio.create_task(s.manager.async_update_config(candidate))
    await entered.wait()
    if cancel:
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await task
    assert s.manager.startup_unavailable
    store.async_save.assert_awaited_once()
    await s.manager.async_reconcile(now=END)
    await s.manager.async_shutdown()
    assert saved == before
    store.async_save.assert_awaited_once()
    s.hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("unavailable", [False, True])
async def test_verified_control_without_satellite_binding_keeps_normal_restoration(
    setup_controls, unavailable
):
    s = setup_controls
    saved = _generation()
    saved["config"]["overrides"] = {}
    saved["config"]["wake_sound"] = "unchanged"
    saved["selection_identities"] = {}
    saved["active"]["controls"] = {MEDIA: _record(verified=True)}
    s.report(MEDIA, 0.2)
    if unavailable:
        s.states[MEDIA].state = "unavailable"
    manager = _loading_manager(s, saved)
    manager._reschedule = Mock()
    await manager.async_setup()
    assert not manager.startup_unavailable
    assert manager.active["controls"][MEDIA]["original_value"] == 0.8
    await manager.async_reconcile(now=END)
    if unavailable:
        assert manager.active["controls"][MEDIA]["restoration_pending"]
        s.report(MEDIA, 0.2)
        await manager.async_reconcile(now=END)
    assert s.calls == [(MEDIA, 0.8)]
    assert manager.active is None


@pytest.mark.parametrize(
    "active",
    [
        None,
        {"controls": {}},
        {"controls": {}, "observed_controls": [MEDIA]},
        {"targets": {}},
    ],
)
async def test_settings_and_observations_without_obligations_are_not_quarantined(
    setup_controls, active
):
    s = setup_controls
    saved = _generation(enabled=False)
    saved["active"] = active
    if active is not None:
        active.update(
            period_started_at=START.isoformat(), period_ends_at=END.isoformat()
        )
    manager = _loading_manager(s, saved)
    manager._reschedule = Mock()
    await manager.async_setup()
    assert not manager.startup_unavailable
    assert manager.config.overrides
    s.hass.services.async_call.assert_not_awaited()


def test_verified_saved_identity_does_not_need_a_current_registry_match(
    setup_controls, monkeypatch
):
    s = setup_controls
    saved = _generation()
    saved["active"]["controls"] = {MEDIA: _record(verified=True)}
    monkeypatch.setattr(
        runtime.er, "async_get", lambda _: pytest.fail("no current registry inference")
    )
    assert s.manager._check_saved_ownership(saved["active"])
