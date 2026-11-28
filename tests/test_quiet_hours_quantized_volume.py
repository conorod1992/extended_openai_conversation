"""Restoration follows owned device reports without claiming manual changes."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from tests.test_quiet_hours_runtime import _install_registered_controls


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
    _install_registered_controls(
        monkeypatch, "media_player.test", "assist_satellite.test"
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


@pytest.mark.parametrize("manual", [False, True])
async def test_delayed_restoration_across_reenable(hass, monkeypatch, manual):
    manager, state, calls, reports, report = _speaker(hass, monkeypatch)
    await manager.async_reconcile(now=START)
    service = hass.services.async_call.side_effect

    async def delay_restore(domain, name, data, **kwargs):
        if data["volume_level"] == 0.8:
            calls.append(0.8)
            reports.append((0.8, kwargs["context"]))
        else:
            await service(domain, name, data, **kwargs)

    hass.services.async_call.side_effect = delay_restore
    manager._config = replace(manager._config, enabled=False)
    await manager.async_reconcile(now=START)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    manager._config = replace(manager._config, enabled=True)
    await manager.async_reconcile(now=START)
    assert calls == [0.23, 0.8]
    restored, context = reports.pop()
    report(restored, Context(user_id="manual") if manual else context)
    await manager.async_reconcile(now=START)
    assert calls == ([0.23, 0.8] if manual else [0.23, 0.8, 0.23])
    assert state.attributes["volume_level"] == (0.8 if manual else 0.25)


async def test_unavailable_service_retries_initial_command(hass, monkeypatch):
    manager, _, calls, _, _ = _speaker(hass, monkeypatch)
    hass.services.has_service.return_value = False
    await manager.async_reconcile(now=START)
    assert calls == []
    assert (
        manager.active["controls"]["media_player.test"]["application_state"]
        == "prepared"
    )
    hass.services.has_service.return_value = True
    await manager.async_reconcile(now=START)
    assert calls == [0.23]


async def test_no_device_report_does_not_repeat_or_restore_the_command(
    hass, monkeypatch
):
    manager, state, calls, reports, _ = _speaker(hass, monkeypatch, delayed=True)
    await manager.async_reconcile(now=START)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=END)
    assert len(reports) == 1
    assert calls == [0.23]
    assert state.attributes["volume_level"] == 0.8


@pytest.mark.parametrize("original", [0.60, 0.24])
async def test_legacy_correction_metadata_preserves_rounded_restoration(
    hass, monkeypatch, original
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch)
    state.attributes["volume_level"] = original
    await manager.async_reconcile(now=START)
    raw = deepcopy(manager.active)
    raw["controls"]["media_player.test"].update(
        ceiling_attempts=2, ceiling_target=0.13, ceiling_settle_until=END.isoformat()
    )
    manager._active = manager._normalize_active(raw)
    control = manager.active["controls"]["media_player.test"]
    assert not any(key.startswith("ceiling_") for key in control)
    assert control["quiet_value"] == 0.23
    assert control["original_value"] == original
    assert control["attained_value"] == pytest.approx(0.25)
    assert control["attained_context_id"] == state.context.id
    state.context = Context()
    await manager.async_reconcile(now=START)
    assert calls == [0.23]
    await manager.async_reconcile(now=END)
    assert calls == [0.23, original]


@pytest.mark.parametrize("requested", [0.2, 0.23])
@pytest.mark.parametrize("restart", [False, True])
async def test_rounding_restores_original_and_survives_storage_roundtrip(
    hass, monkeypatch, requested, restart
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch, requested=requested)
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == pytest.approx(
        round(requested / 0.05) * 0.05
    )
    assert manager.active["controls"]["media_player.test"]["quiet_value"] == requested
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
        state.context = Context()
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.8)
    assert calls == [requested, 0.8]
    assert manager.active is None


async def test_manual_adjustment_after_rounded_application_is_not_restored(
    hass, monkeypatch
):
    manager, state, calls, _, report = _speaker(hass, monkeypatch)
    await manager.async_reconcile(now=START)
    report(0.35, Context())
    await manager.async_reconcile(now=END)
    assert state.attributes["volume_level"] == pytest.approx(0.35)
    assert calls == [0.23]
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
    assert calls == [0.23, 0.8]


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
    assert calls == [0.23, 0.31, 0.8]


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


@pytest.mark.parametrize("legacy_context", [False, True])
async def test_lost_restoration_ack_allows_reentry_with_reported_baseline(
    hass, monkeypatch, legacy_context
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch)
    original = 0.8 if legacy_context else 0.83
    state.attributes["volume_level"] = original
    await manager.async_reconcile(now=START)
    application_context = state.context
    capabilities = manager._discover_capabilities
    manager._discover_capabilities = lambda: []
    service = hass.services.async_call.side_effect

    async def restore_with_lost_ack(*args, **kwargs):
        await service(*args, **kwargs)
        raise HomeAssistantError("Restored volume, but acknowledgement was lost")

    hass.services.async_call.side_effect = restore_with_lost_ack
    await manager.async_reconcile(now=START)
    restored = state.attributes["volume_level"]
    assert restored == pytest.approx(0.8 if legacy_context else 0.85)
    assert state.context.id != application_context.id

    saved = deepcopy(manager.active)
    if legacy_context:
        saved["controls"]["media_player.test"].pop("restoration_context_id")
        state.context = application_context
    manager._active = manager._normalize_active(saved)
    hass.services.async_call.side_effect = service
    manager._discover_capabilities = capabilities

    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=END)
    assert calls == [0.23, original, 0.23, restored]
    assert manager.active is None


async def test_new_value_during_restoration_receipt_save_is_left_untouched(
    hass, monkeypatch
):
    manager, state, calls, _, report = _speaker(hass, monkeypatch)
    await manager.async_reconcile(now=START)
    manager._async_save_locked.side_effect = lambda: report(0.6, Context())

    await manager.async_reconcile(now=END)
    await manager.async_reconcile(now=END)

    assert calls == [0.23]
    assert state.attributes["volume_level"] == pytest.approx(0.6)
    assert manager.active is None


async def test_explicit_user_report_at_retargeted_volume_releases_ownership(
    hass, monkeypatch
):
    manager, state, calls, _, report = _speaker(hass, monkeypatch)
    await manager.async_reconcile(now=START)

    async def acknowledge_without_report(_domain, _name, data, **_kwargs):
        calls.append(data["volume_level"])

    hass.services.async_call.side_effect = acknowledge_without_report
    manager._config = replace(manager.config, max_volume=0.3)
    await manager.async_reconcile(now=START)
    report(0.3, Context(user_id="a" * 32))

    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=END)

    assert calls == [0.23, 0.3]
    assert state.attributes["volume_level"] == pytest.approx(0.3)


@pytest.mark.parametrize("restart", [False, True])
async def test_same_step_retarget_allows_a_later_target(hass, monkeypatch, restart):
    manager, state, calls, _, _ = _speaker(
        hass, monkeypatch, requested=0.21, unchanged_reports=False
    )
    monkeypatch.setattr(runtime.dt_util, "utcnow", lambda: START)
    await manager.async_reconcile(now=START)
    manager._config = replace(manager.config, max_volume=0.22)
    await manager.async_reconcile(now=START)
    if restart:
        manager._active = manager._normalize_active(deepcopy(manager.active))
    manager._config = replace(manager.config, max_volume=0.1)
    await manager.async_reconcile(now=START)
    assert calls == [0.21, 0.22]
    after_grace = START + runtime._CONTROL_FEEDBACK_GRACE
    monkeypatch.setattr(runtime.dt_util, "utcnow", lambda: after_grace)
    await manager.async_reconcile(now=after_grace)
    assert calls == [0.21, 0.22, 0.1]
    assert state.attributes["volume_level"] == 0.1
    await manager.async_reconcile(now=END)
    assert calls[-1] == 0.8


async def test_initial_failure_retries_next_occurrence_with_original_receipt(
    hass, monkeypatch
):
    manager, state, calls, reports, _ = _speaker(
        hass, monkeypatch, delayed=True, lost_ack=True
    )
    await manager.async_reconcile(now=START)
    await manager.async_reconcile(now=END)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    await manager.async_reconcile(now=START + timedelta(days=1))
    assert state.context.id == reports[0][1].id
    await manager.async_reconcile(now=END + timedelta(days=1))
    assert calls == [0.23, 0.23, 0.8]


async def test_failed_retarget_keeps_superseded_receipt_after_restart(
    hass, monkeypatch
):
    manager, state, calls, reports, report = _speaker(hass, monkeypatch, requested=0.2)
    service = hass.services.async_call.side_effect
    await manager.async_reconcile(now=START)

    async def fail_without_report(_domain, _name, data, **kwargs):
        calls.append(data["volume_level"])
        reports.append((data["volume_level"], kwargs["context"]))
        raise HomeAssistantError("Device acknowledgement was lost")

    hass.services.async_call.side_effect = fail_without_report
    for target in (0.3, 0.1):
        manager._config = replace(manager.config, max_volume=target)
        await manager.async_reconcile(now=START)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    report(*reports[0])
    hass.services.async_call.side_effect = service
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == 0.1
    await manager.async_reconcile(now=END)
    assert calls == [0.2, 0.3, 0.1, 0.1, 0.8]


@pytest.mark.parametrize("manual", [False, True])
async def test_unacknowledged_restoration_recovers_after_restart(
    hass, monkeypatch, manual
):
    manager, state, calls, _, report = _speaker(hass, monkeypatch, requested=0.2)
    monkeypatch.setattr(runtime.dt_util, "utcnow", lambda: END)
    await manager.async_reconcile(now=START)
    service = hass.services.async_call.side_effect

    async def ignore(_domain, _name, data, **_kwargs):
        calls.append(data["volume_level"])

    hass.services.async_call.side_effect = ignore
    await manager.async_reconcile(now=END)
    manager._active = manager._normalize_active(deepcopy(manager.active))
    if manual:
        report(0.2, Context(user_id="a" * 32))
    await manager.async_reconcile(now=END)
    assert calls == [0.2, 0.8]
    after_grace = END + runtime._CONTROL_FEEDBACK_GRACE
    monkeypatch.setattr(runtime.dt_util, "utcnow", lambda: after_grace)
    hass.services.async_call.side_effect = service
    await manager.async_reconcile(now=after_grace)
    assert calls == ([0.2, 0.8] if manual else [0.2, 0.8, 0.8])
    assert state.attributes["volume_level"] == (0.2 if manual else 0.8)
    assert manager.active is None


async def test_stalled_service_releases_reconciliation_lock(hass, monkeypatch):
    manager, _, calls, _, _ = _speaker(hass, monkeypatch, requested=0.2)
    release = asyncio.Event()
    cancelled = asyncio.Event()
    monkeypatch.setattr(runtime, "_CONTROL_SERVICE_TIMEOUT", 0.01)

    async def stall(_domain, _name, data, **_kwargs):
        calls.append(data["volume_level"])
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    hass.services.async_call.side_effect = stall
    try:
        await asyncio.wait_for(manager.async_reconcile(now=START), timeout=1)
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        await asyncio.wait_for(manager.async_reconcile(now=END), timeout=1)
        assert calls == [0.2]
        assert manager.active["controls"]["media_player.test"]["restoration_pending"]
    finally:
        release.set()
        if task := manager._pending_service_calls.get("media_player.test"):
            await task
