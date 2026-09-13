"""Residual coverage for the base Quiet Hours runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours_runtime as quiet


class _States:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.removed: list[str] = []

    def get(self, entity_id: str):
        return self.values.get(entity_id)

    def async_all(self, domain=None):
        return [
            state
            for entity_id, state in self.values.items()
            if domain is None or entity_id.startswith(f"{domain}.")
        ]

    def async_set(self, entity_id: str, state: str, attributes=None) -> None:
        self.values[entity_id] = SimpleNamespace(
            entity_id=entity_id,
            state=state,
            attributes=dict(attributes or {}),
        )

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.values.pop(entity_id, None)


def _state(entity_id: str, state: str, **attributes):
    return SimpleNamespace(
        entity_id=entity_id,
        state=state,
        attributes=attributes,
    )


def _hass(states=None):
    return SimpleNamespace(
        data={},
        states=_States(states),
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def _manager(hass=None, *, initialized=True):
    manager = quiet.QuietHoursManager.__new__(quiet.QuietHoursManager)
    manager.hass = hass or _hass()
    manager._store = SimpleNamespace(
        async_load=AsyncMock(return_value={}),
        async_save=AsyncMock(),
    )
    manager._config = quiet.QuietHoursConfig()
    manager._active = None
    manager._lock = asyncio.Lock()
    manager._initialized = initialized
    manager._unsubscribers = []
    return manager


def test_clock_config_and_value_object_edge_cases() -> None:
    override = quiet.SatelliteOverride(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        "switch.bedroom_wake",
    )
    config = quiet.QuietHoursConfig(overrides=(override,))
    assert config.as_dict()["overrides"]["assist_satellite.bedroom"] == {
        "media_player_entity_id": "media_player.bedroom",
        "wake_sound_entity_id": "switch.bedroom_wake",
    }

    capability = quiet.SatelliteCapabilities(
        "assist_satellite.bedroom",
        "Bedroom",
        "device-1",
        "media_player.bedroom",
        "switch.bedroom_wake",
        "auto",
        "manual",
    )
    assert capability.as_dict()["wake_sound_source"] == "manual"

    with pytest.raises(ValueError, match="HH:MM string"):
        quiet._parse_clock(2200)
    with pytest.raises(ValueError, match="HH:MM"):
        quiet._parse_clock("22:00:01")
    with pytest.raises(ValueError, match="timezone-aware"):
        quiet.quiet_period_for(datetime(2026, 9, 13, 23), "22:00", "07:00")
    with pytest.raises(ValueError, match="must differ"):
        quiet.quiet_period_for(
            datetime(2026, 9, 13, 23, tzinfo=UTC), "22:00", "22:00"
        )

    assert quiet.quiet_period_for(
        datetime(2026, 9, 13, 12, tzinfo=UTC), "09:00", "17:00"
    ) is not None
    assert quiet.quiet_period_for(
        datetime(2026, 9, 13, 18, tzinfo=UTC), "09:00", "17:00"
    ) is None
    assert quiet.quiet_period_for(
        datetime(2026, 9, 13, 12, tzinfo=UTC), "22:00", "07:00"
    ) is None


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([], "must be an object"),
        ({"enabled": "yes"}, "boolean"),
        ({"start": "08:00", "end": "08:00"}, "must differ"),
        ({"max_volume": True}, "must be a number"),
        ({"max_volume": float("inf")}, "between 0 and 1"),
        ({"wake_sound_enabled": "yes"}, "boolean"),
        ({"overrides": "bad"}, "must be an object"),
        ({"overrides": {"assist_satellite.bedroom": "bad"}}, "must be an object"),
        (
            {
                "overrides": {
                    "assist_satellite.bedroom": {
                        "wake_sound_entity_id": "light.wrong"
                    }
                }
            },
            "must be a switch entity",
        ),
    ],
)
def test_config_validation_rejects_bad_persisted_values(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        quiet._config_from_data(value)


def test_config_migrates_legacy_overrides_and_wake_flag() -> None:
    config = quiet._config_from_data(
        {
            "volume_level": 0.35,
            "wake_sound_enabled": False,
            "overrides": [
                {
                    "satellite_entity_id": "assist_satellite.bedroom",
                    "media_player_entity_id": "media_player.bedroom",
                },
                "ignored",
                {},
            ],
        }
    )
    assert config.max_volume == 0.35
    assert config.wake_sound == "off"
    assert config.overrides == (
        quiet.SatelliteOverride(
            "assist_satellite.bedroom",
            "media_player.bedroom",
            None,
        ),
    )
    assert quiet._optional_entity("", "switch", "Wake") is None


def test_live_state_readers_and_picker_fallbacks() -> None:
    hass = _hass(
        {
            "media_player.good": _state(
                "media_player.good", "idle", volume_level=0.4
            ),
            "media_player.bool": _state(
                "media_player.bool", "idle", volume_level=True
            ),
            "media_player.range": _state(
                "media_player.range", "idle", volume_level=2.0
            ),
            "switch.on": _state("switch.on", "on"),
            "switch.unknown": _state("switch.unknown", "unknown"),
        }
    )
    assert quiet._safe_iso(None) is None
    assert quiet._safe_iso("2026-09-13T22:00:00") is None
    assert quiet._safe_iso("2026-09-13T22:00:00+00:00") is not None
    assert quiet._current_volume(hass, "media_player.good") == 0.4
    assert quiet._current_volume(hass, "media_player.bool") is None
    assert quiet._current_volume(hass, "media_player.range") is None
    assert quiet._current_volume(hass, "media_player.missing") is None
    assert quiet._current_switch(hass, "switch.on") is True
    assert quiet._current_switch(hass, "switch.unknown") is None

    def entry(entity_id, domain, **kwargs):
        return SimpleNamespace(
            entity_id=entity_id,
            domain=domain,
            disabled_by=kwargs.get("disabled_by"),
            platform=kwargs.get("platform", "other"),
            name=kwargs.get("name"),
            original_name=kwargs.get("name"),
        )

    generic = entry("media_player.good", "media_player")
    speaker = entry(
        "media_player.speaker",
        "media_player",
        platform="esphome",
        name="Bedroom Speaker",
    )
    disabled = entry(
        "media_player.disabled",
        "media_player",
        disabled_by="user",
    )
    assert quiet._pick_media_player(hass, [generic, speaker]) == "media_player.good"
    assert quiet._pick_media_player(hass, [disabled]) is None
    assert quiet._pick_wake_sound([entry("switch.other", "switch", name="Other")]) is None


def test_discovery_skips_disabled_and_handles_unregistered_satellite(monkeypatch) -> None:
    hass = _hass(
        {
            "assist_satellite.disabled": _state(
                "assist_satellite.disabled", "idle"
            ),
            "assist_satellite.unregistered": _state(
                "assist_satellite.unregistered",
                "idle",
                friendly_name="Unregistered",
            ),
        }
    )
    disabled = SimpleNamespace(
        entity_id="assist_satellite.disabled",
        disabled_by="user",
        device_id="device-1",
        name=None,
        original_name=None,
    )
    registry = SimpleNamespace(
        async_get=lambda entity_id: disabled
        if entity_id == "assist_satellite.disabled"
        else None
    )
    monkeypatch.setattr(quiet.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        quiet.er,
        "async_entries_for_device",
        lambda *_args: pytest.fail("no device lookup expected"),
    )

    discovered = quiet.discover_satellite_capabilities(
        hass, quiet.QuietHoursConfig()
    )
    assert len(discovered) == 1
    assert discovered[0].name == "Unregistered"
    assert discovered[0].device_id is None
    assert discovered[0].media_player_entity_id is None


async def test_setup_recovers_invalid_storage_and_is_idempotent() -> None:
    manager = _manager(initialized=False)
    manager._store.async_load.return_value = {
        "config": {"max_volume": 2},
        "active": {"not": "valid"},
    }
    manager._reschedule = Mock()
    manager.async_reconcile = AsyncMock()

    await manager.async_setup()
    assert manager.config == quiet.QuietHoursConfig()
    assert manager.active is None
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()

    await manager.async_setup()
    manager._reschedule.assert_called_once_with()


async def test_update_config_rolls_back_then_succeeds() -> None:
    manager = _manager()
    previous = manager.config
    manager._async_restore_locked = AsyncMock()
    manager._async_save_locked = AsyncMock(side_effect=RuntimeError("disk failed"))
    manager._reschedule = Mock()

    with pytest.raises(RuntimeError, match="disk failed"):
        await manager.async_update_config({"enabled": True, "max_volume": 0.1})
    assert manager.config == previous
    manager._reschedule.assert_not_called()

    manager._async_save_locked = AsyncMock()
    manager.async_reconcile = AsyncMock()
    manager.snapshot = Mock(return_value={"updated": True})
    result = await manager.async_update_config(
        {"enabled": True, "start": "21:00", "end": "06:00"}
    )
    assert result == {"updated": True}
    assert manager.config.enabled is True
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()


async def test_reconcile_covers_disabled_new_and_stale_periods(monkeypatch) -> None:
    manager = _manager(initialized=False)
    manager._publish_state = Mock()
    await manager.async_reconcile(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    manager._publish_state.assert_not_called()

    manager._initialized = True
    manager._config = quiet.QuietHoursConfig(enabled=False)
    manager._async_restore_locked = AsyncMock()
    await manager.async_reconcile(now=datetime(2026, 9, 13, 23, tzinfo=UTC))
    manager._publish_state.assert_called_once_with(None)
    manager._async_restore_locked.assert_awaited_once_with()

    manager._config = quiet.QuietHoursConfig(
        enabled=True,
        start="22:00",
        end="07:00",
        wake_sound="off",
    )
    manager._active = None
    manager._async_restore_locked.reset_mock()
    manager._async_save_locked = AsyncMock()
    manager._async_apply_volume_locked = AsyncMock()
    manager._async_apply_switch_locked = AsyncMock()
    capability = quiet.SatelliteCapabilities(
        "assist_satellite.bedroom",
        "Bedroom",
        "device-1",
        "media_player.bedroom",
        "switch.bedroom_wake",
        "auto",
        "auto",
    )
    monkeypatch.setattr(
        quiet, "discover_satellite_capabilities", lambda *_args: [capability]
    )
    await manager.async_reconcile(
        now=datetime(2026, 9, 13, 23, tzinfo=UTC)
    )
    assert manager._active is not None
    manager._async_apply_volume_locked.assert_awaited_once()
    manager._async_apply_switch_locked.assert_awaited_once()

    old = {
        "period_started_at": "2026-09-12T22:00:00+00:00",
        "controls": {},
    }
    manager._active = old

    async def restore():
        manager._active = None

    manager._async_restore_locked = AsyncMock(side_effect=restore)
    monkeypatch.setattr(quiet, "discover_satellite_capabilities", lambda *_args: [])
    await manager.async_reconcile(
        now=datetime(2026, 9, 13, 23, tzinfo=UTC)
    )
    manager._async_restore_locked.assert_awaited_once_with()
    assert manager._active is not None
    assert manager._active["period_started_at"].startswith("2026-09-13T22:00")


async def test_apply_controls_claim_only_needed_values_and_release_on_failure() -> None:
    hass = _hass(
        {
            "media_player.loud": _state(
                "media_player.loud", "idle", volume_level=0.8
            ),
            "media_player.quiet": _state(
                "media_player.quiet", "idle", volume_level=0.1
            ),
            "switch.on": _state("switch.on", "on"),
            "switch.off": _state("switch.off", "off"),
        }
    )
    manager = _manager(hass)
    manager._config = quiet.QuietHoursConfig(max_volume=0.2)
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    controls = {}

    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.quiet", controls
    )
    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.off", False, controls
    )
    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.on", False, controls
    )
    assert set(controls) == {"media_player.loud", "switch.on"}
    manager._async_set_volume.assert_awaited_once_with("media_player.loud", 0.2)
    manager._async_set_switch.assert_awaited_once_with("switch.on", False)

    controls.clear()
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("offline"))
    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    assert controls == {}


async def test_restore_respects_manual_changes_and_clears_failed_ownership() -> None:
    hass = _hass(
        {
            "media_player.owned": _state(
                "media_player.owned", "idle", volume_level=0.2
            ),
            "media_player.changed": _state(
                "media_player.changed", "idle", volume_level=0.4
            ),
            "switch.owned": _state("switch.owned", "off"),
            "switch.changed": _state("switch.changed", "off"),
        }
    )
    manager = _manager(hass)
    manager._active = {
        "controls": {
            "media_player.owned": {
                "kind": "volume",
                "original_value": 0.7,
                "quiet_value": 0.2,
            },
            "media_player.changed": {
                "kind": "volume",
                "original_value": 0.8,
                "quiet_value": 0.2,
            },
            "switch.owned": {
                "kind": "switch",
                "original_value": True,
                "quiet_value": False,
            },
            "switch.changed": {
                "kind": "switch",
                "original_value": False,
                "quiet_value": True,
            },
            123: {"kind": "switch"},
            "bad": "not-a-control",
        }
    }
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()
    manager._async_set_volume.assert_awaited_once_with("media_player.owned", 0.7)
    manager._async_set_switch.assert_awaited_once_with("switch.owned", True)
    assert manager._active is None

    manager._active = {
        "controls": {
            "media_player.owned": {
                "kind": "volume",
                "original_value": 0.7,
                "quiet_value": 0.2,
            }
        }
    }
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("offline"))
    await manager._async_restore_locked()
    assert manager._active is None


async def test_service_save_shutdown_and_callbacks() -> None:
    manager = _manager()
    manager._config = quiet.QuietHoursConfig(enabled=True, max_volume=0.15)
    manager._active = {"controls": {}}
    await manager._async_save_locked()
    manager._store.async_save.assert_awaited_once()

    await manager._async_set_volume("media_player.bedroom", 0.2)
    await manager._async_set_switch("switch.wake", True)
    await manager._async_set_switch("switch.wake", False)
    assert manager.hass.services.async_call.await_args_list == [
        call(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.bedroom", "volume_level": 0.2},
            blocking=True,
        ),
        call("switch", "turn_on", {"entity_id": "switch.wake"}, blocking=True),
        call("switch", "turn_off", {"entity_id": "switch.wake"}, blocking=True),
    ]

    manager.async_reconcile = AsyncMock()
    now = datetime(2026, 9, 13, 22, tzinfo=UTC)
    await manager._handle_time_transition(now)
    await manager._handle_discovery_tick(now)
    assert manager.async_reconcile.await_args_list == [call(now=now), call(now=now)]

    first = Mock()
    second = Mock()
    manager._unsubscribers = [first, second]
    await manager.async_shutdown()
    first.assert_called_once_with()
    second.assert_called_once_with()
    assert quiet._STATE_ENTITY_ID in manager.hass.states.removed


def test_normalize_active_migrates_legacy_targets_and_filters_invalid() -> None:
    manager = _manager()
    assert manager._normalize_active(None) is None
    assert manager._normalize_active(
        {
            "period_started_at": "bad",
            "period_ends_at": "also-bad",
            "controls": {},
        }
    ) is None
    assert manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-13T21:00:00+00:00",
            "controls": {},
        }
    ) is None

    active = manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-14T07:00:00+00:00",
            "targets": {
                "media_player.bedroom": {
                    "original_volume": 0.8,
                    "quiet_volume": 0.2,
                },
                "media_player.bad": "bad",
            },
        }
    )
    assert active is not None
    assert active["controls"]["media_player.bedroom"]["original_value"] == 0.8

    active = manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-14T07:00:00+00:00",
            "controls": {
                "switch.wake": {
                    "kind": "switch",
                    "original_value": True,
                    "quiet_value": False,
                },
                "media_player.bad": {
                    "kind": "volume",
                    "original_value": 2,
                    "quiet_value": 0.2,
                },
            },
        }
    )
    assert active is not None
    assert set(active["controls"]) == {"switch.wake"}


def test_publish_snapshot_and_reschedule(monkeypatch) -> None:
    manager = _manager()
    manager._config = quiet.QuietHoursConfig(
        enabled=True,
        start="22:00",
        end="07:00",
        max_volume=0.2,
        wake_sound="off",
    )
    period = quiet.QuietPeriod(
        datetime(2026, 9, 13, 22, tzinfo=UTC),
        datetime(2026, 9, 14, 7, tzinfo=UTC),
    )
    manager._publish_state(period)
    assert manager.hass.states.get(quiet._STATE_ENTITY_ID).state == "on"

    monkeypatch.setattr(
        quiet.dt_util,
        "now",
        lambda: datetime(2026, 9, 13, 23, tzinfo=UTC),
    )
    manager.discovery_snapshot = Mock(return_value=[])
    manager._active = {"controls": {"media_player.bedroom": {}}}
    assert manager.snapshot()["owned_controls"] == ["media_player.bedroom"]

    old = Mock()
    manager._unsubscribers = [old]
    manager._config = quiet.QuietHoursConfig(enabled=False)
    manager._reschedule()
    old.assert_called_once_with()
    assert manager._unsubscribers == []

    callbacks = [Mock(), Mock(), Mock()]
    time_change = Mock(side_effect=callbacks[:2])
    interval = Mock(return_value=callbacks[2])
    monkeypatch.setattr(quiet, "async_track_time_change", time_change)
    monkeypatch.setattr(quiet, "async_track_time_interval", interval)
    manager._config = quiet.QuietHoursConfig(
        enabled=True,
        start="22:15",
        end="06:45",
    )
    manager._reschedule()
    assert len(manager._unsubscribers) == 3
    assert time_change.call_count == 2
    interval.assert_called_once_with(
        manager.hass,
        manager._handle_discovery_tick,
        quiet._DISCOVERY_INTERVAL,
    )


async def test_async_get_quiet_hours_reuses_global_manager(monkeypatch) -> None:
    hass = _hass()
    created = []

    class FakeManager:
        def __init__(self, passed_hass) -> None:
            assert passed_hass is hass
            self.setup = AsyncMock()
            created.append(self)

        async def async_setup(self) -> None:
            await self.setup()

    monkeypatch.setattr(quiet, "QuietHoursManager", FakeManager)
    first = await quiet.async_get_quiet_hours(hass)
    second = await quiet.async_get_quiet_hours(hass)
    assert first is second
    assert len(created) == 1
    assert first.setup.await_count == 2
