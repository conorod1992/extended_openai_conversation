"""Restoration follows owned device reports without claiming manual changes."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError


def _speaker(
    hass,
    monkeypatch,
    *,
    requested=0.23,
    delayed=False,
    lost_ack=False,
    unchanged_reports=True,
):
    state = SimpleNamespace(
        state="idle", attributes={"volume_level": 0.8}, context=Context()
    )
    hass.states.get.side_effect = lambda _: state
    monkeypatch.setattr(
        runtime.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None)
    )
    manager = runtime.QuietHoursManager(hass)
    manager._initialized = True
    manager._config = runtime.QuietHoursConfig(
        enabled=True, max_volume=requested, wake_sound="unchanged"
    )
    manager._async_save_locked = AsyncMock()
    manager._async_resolve_control_identities_locked = AsyncMock()
    manager._publish_state = lambda _: None
    manager._discover_capabilities = lambda: [
        runtime.SatelliteCapabilities(
            satellite_entity_id="assist_satellite.test",
            name="Test",
            device_id="device",
            media_player_entity_id="media_player.test",
            wake_sound_entity_id=None,
            media_player_source="auto",
            wake_sound_source=None,
        )
    ]
    calls, reports = [], []

    def report(volume, context):
        attained = round(volume / 0.05) * 0.05
        if not unchanged_reports and attained == state.attributes["volume_level"]:
            return
        state.attributes["volume_level"] = attained
        state.context = context

    async def service(domain, name, data, **kwargs):
        volume = data["volume_level"]
        calls.append(volume)
        if delayed and len(calls) == 1:
            reports.append((volume, kwargs["context"]))
        else:
            report(volume, kwargs["context"])
        if lost_ack and len(calls) == 1:
            raise HomeAssistantError("Device acted, but acknowledgement was lost")

    hass.services.async_call.side_effect = service
    monkeypatch.setattr(runtime.dt_util, "as_local", lambda value: value)
    return manager, state, calls, reports, report


START = datetime(2026, 10, 18, 23, tzinfo=UTC)
END = datetime(2026, 10, 19, 8, tzinfo=UTC)


@pytest.mark.parametrize("original", [0.60, 0.24])
@pytest.mark.parametrize("restart", [False, True])
async def test_upward_rounding_is_corrected_below_ceiling_and_restores(
    hass, monkeypatch, original, restart
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch)
    state.attributes["volume_level"] = original
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] <= manager.config.max_volume
    control = manager.active["controls"]["media_player.test"]
    assert control["quiet_value"] == 0.23
    assert control["original_value"] == original
    assert control["attained_value"] == state.attributes["volume_level"]
    count = len(calls)
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
        state.context = Context()
    await manager.async_reconcile(now=START)
    assert len(calls) == count
    await manager.async_reconcile(now=END)
    assert calls[-1] == original


@pytest.mark.parametrize("manual", [False, True])
async def test_ceiling_correction_is_bounded_when_device_does_not_move(
    hass, monkeypatch, manual
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch)

    async def service(domain, name, data, **kwargs):
        calls.append(data["volume_level"])
        if len(calls) == 1:
            state.attributes["volume_level"] = 0.25
            state.context = kwargs["context"]
        elif manual:
            state.attributes["volume_level"] = 0.4
            state.context = Context()
        # Otherwise subsequent commands succeed but publish no new state.

    hass.services.async_call.side_effect = service
    await manager.async_reconcile(now=START)
    assert 1 < len(calls) <= 4
    count = len(calls)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=START)
    assert len(calls) == count
    await manager.async_reconcile(now=END)
    if manual:
        assert state.attributes["volume_level"] == 0.4
        assert len(calls) == count
    else:
        assert calls[-1] == 0.8


async def test_corrective_effect_with_lost_acknowledgement_keeps_restoration_owner(
    hass, monkeypatch
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch)
    original = hass.services.async_call.side_effect

    async def service(*args, **kwargs):
        await original(*args, **kwargs)
        if len(calls) == 2:
            raise HomeAssistantError("Corrected volume, but acknowledgement was lost")

    hass.services.async_call.side_effect = service
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == pytest.approx(0.2)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    await manager.async_reconcile(now=START)
    assert len(calls) == 2
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == pytest.approx([0.23, 0.18, 0.8])


@pytest.mark.parametrize("requested", [0.2, 0.23])
@pytest.mark.parametrize("restart", [False, True])
async def test_rounding_restores_original_and_survives_storage_roundtrip(
    hass, monkeypatch, requested, restart
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch, requested=requested)
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == pytest.approx(0.2)
    assert manager.active["controls"]["media_player.test"]["quiet_value"] == requested
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
        state.context = Context()
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == pytest.approx(
        [requested, *([0.18] if requested == 0.23 else []), 0.8]
    )
    assert manager.active is None


async def test_manual_adjustment_after_rounded_application_is_not_restored(
    hass, monkeypatch
):
    manager, state, calls, _, report = _speaker(hass, monkeypatch)
    await manager.async_reconcile(now=START)
    report(0.35, Context())
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.35)
    assert calls == pytest.approx([0.23, 0.18])
    assert manager.active is None


@pytest.mark.parametrize("observe_while_active", [False, True])
async def test_delayed_rounded_report_is_attributed_before_restore(
    hass, monkeypatch, observe_while_active
):
    manager, state, calls, reports, report = _speaker(hass, monkeypatch, delayed=True)
    await manager.async_reconcile(now=START)
    report(*reports.pop())
    if observe_while_active:
        await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == pytest.approx(
        [0.23, *([0.18] if observe_while_active else []), 0.8]
    )


async def test_rounded_effect_with_lost_acknowledgement_is_restored(hass, monkeypatch):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch, lost_ack=True)
    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == [0.23, 0.8]


async def test_retarget_uses_new_rounded_value_without_repeated_volume_calls(
    hass, monkeypatch
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch)
    await manager.async_reconcile(now=START)
    manager._config = replace(manager.config, max_volume=0.31)
    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == pytest.approx(0.3)
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == pytest.approx([0.23, 0.18, 0.31, 0.8])


@pytest.mark.parametrize("restart", [False, True])
async def test_retarget_within_same_volume_step_preserves_restoration_baseline(
    hass, monkeypatch, restart
):
    manager, state, calls, _, _ = _speaker(
        hass, monkeypatch, requested=0.21, unchanged_reports=False
    )
    await manager.async_reconcile(now=START)
    owned_context = state.context
    manager._config = replace(manager.config, max_volume=0.22)
    await manager.async_reconcile(now=START)
    assert state.context is owned_context  # No new report or operation attribution.
    control = manager.active["controls"]["media_player.test"]
    assert control["quiet_value"] == 0.22
    assert control["attained_value"] == pytest.approx(0.2)
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
        state.context = Context()
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == [0.21, 0.22, 0.8]


@pytest.mark.parametrize("restart_before_correction", [False, True])
@pytest.mark.parametrize("manual", [False, True])
async def test_lower_same_step_ceiling_retains_owned_report_across_recovery(
    hass, monkeypatch, restart_before_correction, manual
):
    manager, state, calls, _, _ = _speaker(
        hass, monkeypatch, requested=0.21, unchanged_reports=False
    )
    await manager.async_reconcile(now=START)
    owned_context = state.context
    # Model a persisted successful retarget that publishes no new device state.
    # Recovery must distinguish its command identity from the last owned report.
    if restart_before_correction:
        correct = manager._async_correct_volume_ceiling_locked
        manager._async_correct_volume_ceiling_locked = AsyncMock()
    manager._config = replace(manager.config, max_volume=0.19)
    await manager.async_reconcile(now=START)
    if restart_before_correction:
        assert state.context is owned_context
        manager._active = manager._normalize_active(deepcopy(manager.active))
        manager._async_correct_volume_ceiling_locked = correct
    if manual:
        # Even the identical attained value with a different context must not
        # qualify as the old owned report for further corrective commands.
        state.attributes["volume_level"] = 0.2
        state.context = Context()
        count = len(calls)
        await manager.async_reconcile(now=START)
        assert len(calls) == count
        return
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == pytest.approx(0.15)
    assert calls == pytest.approx([0.21, 0.19, 0.16])
    manager._active = manager._normalize_active(deepcopy(manager.active))
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == pytest.approx([0.21, 0.19, 0.16, 0.8])
