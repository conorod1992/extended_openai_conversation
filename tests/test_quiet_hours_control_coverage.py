"""Focused stable coverage for Quiet Hours control ownership and recovery."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_quiet_hours_restoration_regressions import _active
from tests.test_quiet_hours_runtime import _stateful_public_manager


def _state(context_id: str, *, state: str = "idle", volume: float = 0.8):
    return SimpleNamespace(
        state=state,
        attributes={"volume_level": volume},
        context=SimpleNamespace(id=context_id),
    )


@pytest.mark.asyncio
async def test_prepare_control_records_observation_without_claim_when_no_action(
    hass,
) -> None:
    manager = _stateful_public_manager(hass)
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": {},
        "observed_controls": [],
    }
    hass.states.get.side_effect = lambda _entity_id: _state("baseline")
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        manager._active["controls"],
        "volume",
        0.1,
        0.2,
        False,
    )

    assert result is None
    assert manager._active["observed_controls"] == ["media_player.bedroom"]
    assert manager._active["pending_controls"] == {}
    assert manager._active["controls"] == {}
    manager._async_save_control_state_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_control_persists_pending_then_prepared_ownership(hass) -> None:
    manager = _stateful_public_manager(hass)
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": {},
        "observed_controls": [],
    }
    hass.states.get.side_effect = lambda _entity_id: _state("baseline")
    manager._async_save_control_state_locked = AsyncMock()

    control = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        manager._active["controls"],
        "volume",
        0.8,
        0.2,
        True,
    )

    assert control is manager._active["controls"]["media_player.bedroom"]
    assert control["application_state"] == "prepared"
    assert control["baseline_context_id"] == "baseline"
    assert isinstance(control["application_context_id"], str)
    assert manager._active["pending_controls"] == {}
    assert manager._active["observed_controls"] == ["media_player.bedroom"]
    assert manager._async_save_control_state_locked.await_count == 2


@pytest.mark.asyncio
async def test_prepare_control_reconciles_lost_acknowledgement_without_replay(
    hass,
) -> None:
    manager = _stateful_public_manager(hass)
    control = {
        "kind": "volume",
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": 0.8,
        "quiet_value": 0.2,
        "baseline_context_id": "baseline",
        "application_state": "prepared",
        "application_context_id": "apply-context",
    }
    controls = {"media_player.bedroom": control}
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": controls,
        "observed_controls": ["media_player.bedroom"],
        "pending_controls": {},
    }
    hass.states.get.side_effect = lambda _entity_id: _state("apply-context", volume=0.2)
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        controls,
        "volume",
        0.2,
        0.2,
        True,
    )

    assert result is None
    assert control["application_state"] == "applied"
    manager._async_save_control_state_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_control_releases_stale_prepared_owner(hass) -> None:
    manager = _stateful_public_manager(hass)
    controls = {
        "media_player.bedroom": {
            "kind": "volume",
            "satellite_entity_id": "assist_satellite.old",
            "original_value": 0.8,
            "quiet_value": 0.2,
            "baseline_context_id": "baseline",
            "application_state": "prepared",
            "application_context_id": "apply-context",
        }
    }
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": controls,
        "observed_controls": ["media_player.bedroom"],
        "pending_controls": {},
    }
    hass.states.get.side_effect = lambda _entity_id: _state("baseline", volume=0.8)
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        controls,
        "volume",
        0.8,
        0.2,
        True,
    )

    assert result is None
    assert controls == {}
    manager._async_save_control_state_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_control_discards_stale_pending_intent(hass) -> None:
    manager = _stateful_public_manager(hass)
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": {},
        "observed_controls": ["media_player.bedroom"],
        "pending_controls": {
            "media_player.bedroom": {
                "kind": "switch",
                "satellite_entity_id": "assist_satellite.bedroom",
                "original_value": True,
                "quiet_value": False,
                "baseline_context_id": "baseline",
            }
        },
    }
    hass.states.get.side_effect = lambda _entity_id: _state("baseline", volume=0.8)
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        manager._active["controls"],
        "volume",
        0.8,
        0.2,
        True,
    )

    assert result is None
    assert manager._active["pending_controls"] == {}
    manager._async_save_control_state_locked.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_control_reuses_pending_intent_at_unchanged_baseline(hass) -> None:
    entity_id = "media_player.bedroom"
    manager = _stateful_public_manager(hass)
    intent = {
        "kind": "volume",
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": 0.8,
        "quiet_value": 0.2,
        "baseline_context_id": "baseline",
    }
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": {},
        "observed_controls": [entity_id],
        "pending_controls": {entity_id: intent},
    }
    hass.states.get.side_effect = lambda _entity_id: _state("baseline", volume=0.8)
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        entity_id,
        manager._active["controls"],
        "volume",
        0.8,
        0.2,
        True,
    )

    assert result is manager._active["controls"][entity_id]
    assert result["application_state"] == "prepared"
    assert result["baseline_context_id"] == "baseline"
    assert manager._active["pending_controls"] == {}
    assert manager._async_save_control_state_locked.await_count == 2


@pytest.mark.asyncio
async def test_prepare_control_does_not_reopen_applied_ownership(hass) -> None:
    entity_id = "switch.wake_sound"
    manager = _stateful_public_manager(hass)
    control = {
        "kind": "switch",
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": True,
        "quiet_value": False,
        "application_state": "applied",
    }
    manager._active = {"observed_controls": [entity_id], "pending_controls": {}}
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        entity_id,
        {entity_id: control},
        "switch",
        True,
        False,
        True,
    )

    assert result is None
    manager._async_save_control_state_locked.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_apply_skips_service_when_control_changes_before_revalidation(
    hass, kind
) -> None:
    entity_id = "media_player.bedroom" if kind == "volume" else "switch.wake_sound"
    control = {
        "kind": kind,
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": 0.8 if kind == "volume" else True,
        "quiet_value": 0.2 if kind == "volume" else False,
        "application_state": "prepared",
    }
    manager = _stateful_public_manager(hass)
    manager._active = {"observed_controls": [entity_id], "pending_controls": {}}
    controls = {entity_id: control}
    hass.states.get.side_effect = lambda _entity_id: _state(
        "baseline", state="on", volume=0.8
    )
    manager._async_prepare_control_locked = AsyncMock(return_value=control)
    manager._async_revalidate_control_locked = AsyncMock(return_value=False)
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()

    if kind == "volume":
        await manager._async_apply_volume_locked(
            "assist_satellite.bedroom", entity_id, controls
        )
        manager._async_set_volume.assert_not_awaited()
    else:
        await manager._async_apply_switch_locked(
            "assist_satellite.bedroom", entity_id, False, controls
        )
        manager._async_set_switch.assert_not_awaited()

    manager._async_revalidate_control_locked.assert_awaited_once_with(
        entity_id, controls, control
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_failed_apply_releases_ownership_when_device_stayed_unchanged(
    hass, kind
) -> None:
    entity_id = "media_player.bedroom" if kind == "volume" else "switch.wake_sound"
    original = 0.8 if kind == "volume" else True
    quiet_value = 0.2 if kind == "volume" else False
    control = {
        "kind": kind,
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": original,
        "quiet_value": quiet_value,
        "baseline_context_id": "baseline",
        "application_state": "prepared",
        "application_context_id": "operation",
    }
    manager = _stateful_public_manager(hass)
    manager._active = {
        "observed_controls": [entity_id],
        "pending_controls": {},
        "controls": {entity_id: control},
    }
    controls = manager._active["controls"]
    hass.states.get.side_effect = lambda _entity_id: _state(
        "baseline", state="on" if kind == "switch" else "idle", volume=0.8
    )
    manager._async_prepare_control_locked = AsyncMock(return_value=control)
    manager._async_revalidate_control_locked = AsyncMock(return_value=True)
    manager._async_save_control_state_locked = AsyncMock()
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("offline"))
    manager._async_set_switch = AsyncMock(side_effect=RuntimeError("offline"))

    if kind == "volume":
        await manager._async_apply_volume_locked(
            "assist_satellite.bedroom", entity_id, controls
        )
    else:
        await manager._async_apply_switch_locked(
            "assist_satellite.bedroom", entity_id, quiet_value, controls
        )

    assert controls == {}
    assert manager._active["observed_controls"] == []
    manager._async_save_control_state_locked.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_apply_does_not_recreate_ownership_removed_during_service_call(hass) -> None:
    entity_id = "switch.wake_sound"
    control = {
        "kind": "switch",
        "satellite_entity_id": "assist_satellite.bedroom",
        "original_value": True,
        "quiet_value": False,
        "application_state": "prepared",
    }
    manager = _stateful_public_manager(hass)
    controls = {entity_id: control}
    manager._active = {"controls": controls, "observed_controls": [entity_id]}
    hass.states.get.side_effect = lambda _entity_id: _state(
        "baseline", state="on", volume=0.8
    )
    manager._async_prepare_control_locked = AsyncMock(return_value=control)
    manager._async_revalidate_control_locked = AsyncMock(return_value=True)

    async def complete_after_removal(_entity_id, _enabled):
        controls.pop(entity_id)

    manager._async_set_switch = AsyncMock(side_effect=complete_after_removal)
    manager._async_save_control_state_locked = AsyncMock()

    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", entity_id, False, controls
    )

    assert controls == {}
    assert control["application_state"] == "prepared"
    manager._async_save_control_state_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_control_does_not_reclaim_already_observed_control(hass) -> None:
    manager = _stateful_public_manager(hass)
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": {},
        "observed_controls": ["media_player.bedroom"],
        "pending_controls": {},
    }
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_prepare_control_locked(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        manager._active["controls"],
        "volume",
        0.8,
        0.2,
        True,
    )

    assert result is None
    manager._async_save_control_state_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_save_failure_reloads_durable_generation(hass) -> None:
    manager = _stateful_public_manager(hass)
    durable = _active("media_player.bedroom", "volume")
    manager._async_save_locked = AsyncMock(side_effect=OSError("ack lost"))
    manager._store.async_load = AsyncMock(return_value={"active": deepcopy(durable)})

    with pytest.raises(OSError, match="ack lost"):
        await manager._async_save_control_state_locked()

    assert manager._active is not None
    assert manager._active["controls"]["media_player.bedroom"]["original_value"] == 0.8
    assert manager._initialized is True


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [None, {"active": "broken"}])
async def test_control_save_failure_invalidates_unreadable_generation(
    hass, persisted
) -> None:
    manager = _stateful_public_manager(hass)
    manager._active = _active("media_player.bedroom", "volume")
    manager._async_save_locked = AsyncMock(side_effect=OSError("ack lost"))
    manager._store.async_load = AsyncMock(return_value=persisted)

    with pytest.raises(OSError, match="ack lost"):
        await manager._async_save_control_state_locked()

    assert manager._active is None
    assert manager._initialized is False


@pytest.mark.asyncio
async def test_restore_drops_unacknowledged_control_after_context_changes(hass) -> None:
    manager = _stateful_public_manager(hass)
    entity_id = "media_player.bedroom"
    manager._active = _active(entity_id, "volume")
    control = manager._active["controls"][entity_id]
    control.update(
        application_state="prepared",
        application_context_id="quiet-hours-operation",
    )
    hass.states.get.side_effect = lambda _entity_id: _state(
        "unrelated-operation", state="playing", volume=0.2
    )
    manager._async_set_volume = AsyncMock()

    await manager._async_restore_locked()

    assert manager.active is None
    manager._async_set_volume.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_state", [None, "unavailable", "unknown"])
async def test_restore_keeps_prepared_control_when_entity_is_missing_or_unavailable(
    hass, entity_state
) -> None:
    manager = _stateful_public_manager(hass)
    entity_id = "media_player.bedroom"
    manager._active = _active(entity_id, "volume")
    manager._active["controls"][entity_id].update(
        application_state="prepared",
        application_context_id="quiet-hours-operation",
    )
    hass.states.get.side_effect = lambda _entity_id: (
        None
        if entity_state is None
        else _state("quiet-hours-operation", state=entity_state, volume=0.2)
    )
    manager._async_set_volume = AsyncMock()

    await manager._async_restore_locked()

    assert manager.active["controls"][entity_id]["restoration_pending"] is True
    manager._async_set_volume.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_reverts_prepared_control_owned_by_quiet_hours(hass) -> None:
    manager = _stateful_public_manager(hass)
    entity_id = "media_player.bedroom"
    manager._active = _active(entity_id, "volume")
    manager._active["controls"][entity_id].update(
        application_state="prepared",
        application_context_id="quiet-hours-operation",
    )
    hass.states.get.side_effect = lambda _entity_id: _state(
        "quiet-hours-operation", state="playing", volume=0.2
    )
    manager._async_set_volume = AsyncMock()

    await manager._async_restore_locked()

    manager._async_set_volume.assert_awaited_once_with(entity_id, 0.8)
    assert manager.active is None


@pytest.mark.asyncio
async def test_revalidate_releases_control_after_manual_value_change(hass) -> None:
    manager = _stateful_public_manager(hass)
    entity_id = "media_player.bedroom"
    control = {
        "kind": "volume",
        "original_value": 0.8,
        "quiet_value": 0.2,
        "baseline_context_id": "baseline",
    }
    controls = {entity_id: control}
    manager._active = {
        "period_started_at": "2026-10-05T22:00:00+00:00",
        "period_ends_at": "2026-10-06T07:00:00+00:00",
        "controls": controls,
        "observed_controls": [entity_id],
    }
    hass.states.get.side_effect = lambda _entity_id: _state(
        "manual-change", state="playing", volume=0.6
    )
    manager._async_save_control_state_locked = AsyncMock()

    result = await manager._async_revalidate_control_locked(
        entity_id, controls, control
    )

    assert result is False
    assert controls == {}
    assert manager._active["observed_controls"] == [entity_id]
    manager._async_save_control_state_locked.assert_awaited_once()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("application_state", "unknown", "application state"),
        ("application_context_id", "", "application context"),
        ("baseline_context_id", "", "baseline context"),
    ],
)
def test_normalize_active_rejects_invalid_control_metadata(
    hass, field, value, message
) -> None:
    manager = _stateful_public_manager(hass)
    active = _active("media_player.bedroom", "volume")
    active["controls"]["media_player.bedroom"][field] = value

    with pytest.raises(ValueError, match=message):
        manager._normalize_active(active)


def test_normalize_active_keeps_distinct_pending_control_and_observation(hass) -> None:
    manager = _stateful_public_manager(hass)
    active = _active("media_player.bedroom", "volume")
    active["controls"]["media_player.bedroom"]["application_state"] = "applied"
    active["controls"]["media_player.bedroom"]["application_context_id"] = "context-a"
    active["controls"]["media_player.bedroom"]["baseline_context_id"] = "base-a"
    active["observed_controls"] = ["media_player.bedroom", 123]
    active["pending_controls"] = {
        "switch.bedroom_wake_sound": {
            "kind": "switch",
            "satellite_entity_id": "assist_satellite.bedroom",
            "original_value": True,
            "quiet_value": False,
            "baseline_context_id": "base-b",
        }
    }

    normalized = manager._normalize_active(active)

    assert normalized is not None
    assert normalized["observed_controls"] == [
        "media_player.bedroom",
        "switch.bedroom_wake_sound",
    ]
    assert (
        normalized["controls"]["media_player.bedroom"]["application_context_id"]
        == "context-a"
    )
    assert (
        normalized["pending_controls"]["switch.bedroom_wake_sound"][
            "baseline_context_id"
        ]
        == "base-b"
    )


def test_control_value_comparison_handles_volume_and_switch(hass) -> None:
    manager = _stateful_public_manager(hass)

    assert manager._control_values_equal("volume", 0.2, 0.2005)
    assert not manager._control_values_equal("volume", None, 0.2)
    assert manager._control_values_equal("switch", True, True)
    assert not manager._control_values_equal("switch", True, False)
