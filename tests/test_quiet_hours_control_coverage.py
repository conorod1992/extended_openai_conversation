"""Focused stable coverage for Quiet Hours control ownership and recovery."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursConfig,
)
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
    hass.states.get.side_effect = lambda _entity_id: _state(
        "apply-context", volume=0.2
    )
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
    manager._store.async_load = AsyncMock(
        return_value={"active": deepcopy(durable)}
    )

    with pytest.raises(OSError, match="ack lost"):
        await manager._async_save_control_state_locked()

    assert manager._active is not None
    assert manager._active["controls"]["media_player.bedroom"]["original_value"] == 0.8
    assert manager._initialized is True


@pytest.mark.asyncio
async def test_control_save_failure_invalidates_unreadable_generation(hass) -> None:
    manager = _stateful_public_manager(hass)
    manager._active = _active("media_player.bedroom", "volume")
    manager._async_save_locked = AsyncMock(side_effect=OSError("ack lost"))
    manager._store.async_load = AsyncMock(return_value={"active": "broken"})

    with pytest.raises(OSError, match="ack lost"):
        await manager._async_save_control_state_locked()

    assert manager._active is None
    assert manager._initialized is False


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
    assert normalized["pending_controls"]["switch.bedroom_wake_sound"][
        "baseline_context_id"
    ] == "base-b"


def test_control_value_comparison_handles_volume_and_switch(hass) -> None:
    manager = _stateful_public_manager(hass)

    assert manager._control_values_equal("volume", 0.2, 0.2005)
    assert not manager._control_values_equal("volume", None, 0.2)
    assert manager._control_values_equal("switch", True, True)
    assert not manager._control_values_equal("switch", True, False)
