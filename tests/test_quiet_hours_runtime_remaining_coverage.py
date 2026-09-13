"""Residual coverage for Quiet Hours runtime helpers and lifecycle edges."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours_runtime as runtime
from custom_components.extended_openai_conversation_responses.quiet_hours_runtime import (
    QuietHoursConfig,
    QuietHoursManager,
    QuietPeriod,
    SatelliteCapabilities,
)


def _bare_manager(hass: Any, config: QuietHoursConfig | None = None) -> QuietHoursManager:
    manager = object.__new__(QuietHoursManager)
    manager.hass = hass
    manager._config = config or QuietHoursConfig()
    manager._active = None
    manager._lock = asyncio.Lock()
    manager._initialized = True
    manager._unsubscribers = []
    return manager


def test_parse_clock_rejects_invalid_iso_time() -> None:
    with pytest.raises(ValueError, match="time must use HH:MM"):
        runtime._parse_clock("not-a-time")


def test_config_rejects_more_than_100_overrides() -> None:
    overrides = {
        f"assist_satellite.room_{index}": {}
        for index in range(101)
    }

    with pytest.raises(ValueError, match="Select no more than 100 satellite overrides"):
        runtime._config_from_data({"overrides": overrides})


def test_current_switch_returns_none_for_missing_entity() -> None:
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: None))

    assert runtime._current_switch(hass, "switch.missing") is None


def test_discovery_snapshot_serializes_discovered_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_manager(SimpleNamespace())
    capability = SatelliteCapabilities(
        satellite_entity_id="assist_satellite.kitchen",
        name="Kitchen",
        device_id="device-1",
        media_player_entity_id="media_player.kitchen",
        wake_sound_entity_id="switch.kitchen_wake",
        media_player_source="auto",
        wake_sound_source="manual",
    )
    monkeypatch.setattr(
        runtime,
        "discover_satellite_capabilities",
        lambda _hass, _config: [capability],
    )

    assert manager.discovery_snapshot() == [capability.as_dict()]


@pytest.mark.asyncio
async def test_reconcile_reuses_existing_active_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 14, 23, tzinfo=UTC)
    period = QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    existing_controls = {"existing": {"kind": "volume"}}
    manager = _bare_manager(SimpleNamespace(), QuietHoursConfig(enabled=True))
    manager._active = {
        "period_started_at": period.start.isoformat(),
        "period_ends_at": period.end.isoformat(),
        "applied_at": now.isoformat(),
        "controls": existing_controls,
    }
    original_active = manager._active
    manager._publish_state = MagicMock()
    manager._async_restore_locked = AsyncMock()
    manager._async_save_locked = AsyncMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    monkeypatch.setattr(runtime, "quiet_period_for", lambda *_args: period)
    monkeypatch.setattr(runtime, "discover_satellite_capabilities", lambda *_args: [])

    await manager.async_reconcile(now=now)

    assert manager._active is original_active
    manager._async_restore_locked.assert_not_awaited()
    manager._async_save_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_handles_capabilities_without_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 14, 23, tzinfo=UTC)
    period = QuietPeriod(now - timedelta(hours=1), now + timedelta(hours=8))
    manager = _bare_manager(
        SimpleNamespace(),
        QuietHoursConfig(enabled=True, wake_sound="unchanged"),
    )
    manager._active = {
        "period_started_at": period.start.isoformat(),
        "period_ends_at": period.end.isoformat(),
        "applied_at": now.isoformat(),
        "controls": {},
    }
    manager._publish_state = MagicMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    monkeypatch.setattr(runtime, "quiet_period_for", lambda *_args: period)
    monkeypatch.setattr(
        runtime,
        "discover_satellite_capabilities",
        lambda *_args: [
            SatelliteCapabilities(
                satellite_entity_id="assist_satellite.none",
                name="None",
                device_id=None,
                media_player_entity_id=None,
                wake_sound_entity_id=None,
                media_player_source=None,
                wake_sound_source=None,
            ),
            SatelliteCapabilities(
                satellite_entity_id="assist_satellite.wake_only",
                name="Wake only",
                device_id=None,
                media_player_entity_id=None,
                wake_sound_entity_id="switch.wake",
                media_player_source=None,
                wake_sound_source="auto",
            ),
        ],
    )

    await manager.async_reconcile(now=now)

    manager._async_apply_volume_locked.assert_not_awaited()
    manager._async_apply_switch_locked.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["volume", "switch"])
async def test_apply_control_skips_already_owned_entity(kind: str) -> None:
    states = SimpleNamespace(get=MagicMock())
    manager = _bare_manager(SimpleNamespace(states=states))
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    controls = {"entity.test": {"kind": kind}}

    if kind == "volume":
        await manager._async_apply_volume_locked(
            "assist_satellite.test", "entity.test", controls
        )
        manager._async_set_volume.assert_not_awaited()
    else:
        await manager._async_apply_switch_locked(
            "assist_satellite.test", "entity.test", False, controls
        )
        manager._async_set_switch.assert_not_awaited()

    states.get.assert_not_called()
    manager._async_save_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_switch_failure_releases_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _bare_manager(SimpleNamespace())
    controls: dict[str, Any] = {}
    manager._async_save_locked = AsyncMock()
    manager._async_set_switch = AsyncMock(side_effect=RuntimeError("failed"))
    monkeypatch.setattr(runtime, "_current_switch", lambda *_args: True)

    await manager._async_apply_switch_locked(
        "assist_satellite.test", "switch.test", False, controls
    )

    assert "switch.test" not in controls
    assert manager._async_save_locked.await_count == 2


@pytest.mark.asyncio
async def test_restore_with_non_mapping_controls_clears_active_state() -> None:
    manager = _bare_manager(SimpleNamespace())
    manager._active = {"controls": ["bad"]}
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    assert manager._active is None
    manager._async_save_locked.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_restore_ignores_unknown_control_kind_and_continues() -> None:
    manager = _bare_manager(SimpleNamespace())
    manager._active = {
        "controls": {
            "entity.unknown": {
                "kind": "unknown",
                "original_value": True,
                "quiet_value": False,
            },
            "entity.second": {
                "kind": "unknown",
                "original_value": 0.7,
                "quiet_value": 0.2,
            },
        }
    }
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    manager._async_set_volume.assert_not_awaited()
    manager._async_set_switch.assert_not_awaited()
    assert manager._active is None
    manager._async_save_locked.assert_awaited_once_with()


@pytest.mark.parametrize(
    "raw_controls, expected_ids",
    [
        (["not-a-mapping"], None),
        (
            {
                123: {
                    "kind": "volume",
                    "satellite_entity_id": "",
                    "original_value": 0.7,
                    "quiet_value": 0.2,
                },
                "media_player.valid": {
                    "kind": "volume",
                    "satellite_entity_id": "assist_satellite.valid",
                    "original_value": 0.7,
                    "quiet_value": 0.2,
                },
            },
            {"media_player.valid"},
        ),
        (
            {
                "media_player.bad": "not-a-mapping",
                "media_player.valid": {
                    "kind": "volume",
                    "satellite_entity_id": "assist_satellite.valid",
                    "original_value": 0.7,
                    "quiet_value": 0.2,
                },
            },
            {"media_player.valid"},
        ),
    ],
)
def test_normalize_active_rejects_invalid_controls(
    raw_controls: Any, expected_ids: set[str] | None
) -> None:
    manager = _bare_manager(SimpleNamespace())
    value = {
        "period_started_at": "2026-09-14T22:00:00+00:00",
        "period_ends_at": "2026-09-15T07:00:00+00:00",
        "controls": raw_controls,
    }

    normalized = manager._normalize_active(value)

    if expected_ids is None:
        assert normalized is None
    else:
        assert normalized is not None
        assert set(normalized["controls"]) == expected_ids
