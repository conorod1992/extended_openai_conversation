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


def _speaker(hass, monkeypatch, *, requested=0.23, delayed=False, lost_ack=False):
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
        state.attributes["volume_level"] = round(volume / 0.05) * 0.05
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


@pytest.mark.parametrize("requested", [0.2, 0.23])
@pytest.mark.parametrize("restart", [False, True])
async def test_rounding_restores_original_and_survives_storage_roundtrip(
    hass, monkeypatch, requested, restart
):
    manager, state, calls, _, _ = _speaker(hass, monkeypatch, requested=requested)
    await manager.async_reconcile(now=START)
    assert state.attributes["volume_level"] == pytest.approx(
        0.2 if requested == 0.2 else 0.25
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
