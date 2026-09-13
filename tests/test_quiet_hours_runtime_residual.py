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


def test_value_objects_and_clock_validation_cover_edge_forms() -> None:
    override = quiet.SatelliteOverride(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        "switch.bedroom_wake",
    )
    config = quiet.QuietHoursConfig(overrides=(override,))
    assert config.as_dict()["overrides"] == {
        "assist_satellite.bedroom": {
            "media_player_entity_id": "media_player.bedroom",
            "wake_sound_entity_id": "switch.bedroom_wake",
        }
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
        quiet._parse_clock("not-a-time")
    with pytest.raises(ValueError, match="HH:MM"):
        quiet._parse_clock("22:00:01")
    with pytest.raises(ValueError, match="timezone-aware"):
        quiet.quiet_period_for(datetime(2026, 9, 13, 23), "22:00", "07:00")
    with pytest.raises(ValueError, match="must differ"):
        quiet.quiet_period_for(
            datetime(2026, 9, 13, 23, tzinfo=UTC), "22:00", "22:00"
        )

    daytime = quiet.quiet_period_for(
        datetime(2026, 9, 13, 12, tzinfo=UTC), "09:00", "17:00"
    )
    assert daytime is not None
    assert daytime.start.hour == 9
    assert daytime.end.hour == 17
    assert (
        quiet.quiet_period_for(
            datetime(2026, 9, 13, 18, tzinfo=UTC), "09:00", "17:00"
        )
        is None
    )
    assert (
        quiet.quiet_period_for(
            datetime(2026, 9, 13, 12, tzinfo=UTC), "22:00", "07:00"
        )
        is None
    )


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
        (
            {
                "overrides": {
                    f"assist_satellite.satellite_{index}": {}
                    for index in range(101)
                }
            },
            "no more than 100",
        ),
        (
            {"overrides": {"assist_satellite.bedroom": "bad"}},
            "must be an object",
        ),
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
def test_config_validation_rejects_malformed_persisted_values(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        quiet._config_from_data(value)


def test_config_migrates_list_overrides_and_legacy_wake_flag() -> None:
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


def test_safe_iso_and_live_control_readers_reject_bad_state() -> None:
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
    assert quiet._current_volume(hass, "media_player.missing") is None
    assert quiet._current_volume(hass, "media_player.good") == 0.4
    assert quiet._current_volume(hass, "media_player.bool") is None
    assert quiet._current_volume(hass, "media_player.range") is None
    assert quiet._current_switch(hass, "switch.on") is True
    assert quiet._current_switch(hass, "switch.unknown") is None
    assert quiet._current_switch(hass, "switch.missing") is None


def _entry(
    entity_id: str,
    domain: str,
    *,
    platform: str = "other",
    disabled_by=None,
    name=None,
):
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        platform=platform,
        disabled_by=disabled_by,
        name=name,
        original_name=name,
        device_id="device-1",
    )


def test_control_picker_priorities_and_empty_candidates() -> None:
    hass = _hass(
        {
            "media_player.generic": _state(
                "media_player.generic", "idle", volume_level=0.5
            ),
            "media_player.voice": _state("media_player.voice", "idle"),
        }
    )
    disabled = _entry(
        "media_player.disabled",
        "media_player",
        disabled_by="user",
    )
    generic = _entry("media_player.generic", "media_player")
    voice = _entry(
        "media_player.voice",
        "media_player",
        platform="esphome",
        name="Bedroom Speaker",
    )
    assert quiet._pick_media_player(hass, [disabled, generic, voice]) == (
        "media_player.generic"
    )
    assert quiet._pick_media_player(hass, [disabled]) is None

    wake_other = _entry("switch.other", "switch", name="Other")
    wake_generic = _entry(
        "switch.generic_wake",
        "switch",
        name="Wake sound",
    )
    wake_esphome = _entry(
        "switch.esphome_wake",
        "switch",
        platform="esphome",
        name="Wake sound",
    )
    assert quiet._pick_wake_sound([wake_other]) is None
    assert quiet._pick_wake_sound([wake_generic, wake_esphome]) == (
        "switch.esphome_wake"
    )


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
    disabled = _entry(
        "assist_satellite.disabled",
        "assist_satellite",
        disabled_by="user",
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
    item = discovered[0]
    assert item.satellite_entity_id == "assist_satellite.unregistered"
    assert item.name == "Unregistered"
    assert item.device_id is None
    assert item.media_player_entity_id is None
    assert item.wake_sound_entity_id is None


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
    assert manager._initialized is True
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()

    await manager.async_setup()
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()


async def test_shutdown_unsubscribes_and_removes_state_entity() -> None:
    manager = _manager()
    first = Mock()
    second = Mock()
    manager._unsubscribers = [first, second]
    manager.hass.states.async_set(quiet._STATE_ENTITY_ID, "on")

    await manager.async_shutdown()

    first.assert_called_once_with()
    second.assert_called_once_with()
    assert manager._unsubscribers == []
    assert quiet._STATE_ENTITY_ID in manager.hass.states.removed


def test_active_property_is_a_defensive_copy() -> None:
    manager = _manager()
    manager._active = {
        "period_started_at": "start",
        "controls": {"media_player.bedroom": {"kind": "volume"}},
    }
    snapshot = manager.active
    assert snapshot is not None
    snapshot["controls"]["media_player.bedroom"]["kind"] = "changed"
    assert manager._active["controls"]["media_player.bedroom"]["kind"] == "volume"


async def test_update_config_rolls_back_when_persistence_fails() -> None:
    manager = _manager()
    previous = manager.config
    manager._async_restore_locked = AsyncMock()
    manager._async_save_locked = AsyncMock(side_effect=RuntimeError("disk failed"))
    manager._reschedule = Mock()

    with pytest.raises(RuntimeError, match="disk failed"):
        await manager.async_update_config({"enabled": True, "max_volume": 0.1})

    assert manager.config == previous
    manager._reschedule.assert_not_called()


async def test_update_config_persists_reschedules_and_reconciles() -> None:
    manager = _manager()
    manager._async_restore_locked = AsyncMock()
    manager._async_save_locked = AsyncMock()
    manager._reschedule = Mock()
    manager.async_reconcile = AsyncMock()
    manager.snapshot = Mock(return_value={"updated": True})

    result = await manager.async_update_config(
        {"enabled": True, "start": "21:00", "end": "06:00"}
    )

    assert manager.config.enabled is True
    assert manager.config.start == "21:00"
    manager._async_restore_locked.assert_awaited_once_with()
    manager._async_save_locked.assert_awaited_once_with()
    manager._reschedule.assert_called_once_with()
    manager.async_reconcile.assert_awaited_once_with()
    assert result == {"updated": True}


async def test_reconcile_handles_uninitialized_disabled_and_new_period(monkeypatch) -> None:
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
    manager._publish_state.reset_mock()
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

    now = datetime(2026, 9, 13, 23, tzinfo=UTC)
    await manager.async_reconcile(now=now)

    assert manager._active is not None
    assert manager._active["period_started_at"].startswith("2026-09-13T22:00")
    manager._async_save_locked.assert_awaited_once_with()
    manager._async_apply_volume_locked.assert_awaited_once()
    manager._async_apply_switch_locked.assert_awaited_once()


async def test_reconcile_restores_stale_period_before_starting_new_one(monkeypatch) -> None:
    manager = _manager()
    manager._config = quiet.QuietHoursConfig(
        enabled=True,
        start="22:00",
        end="07:00",
        wake_sound="unchanged",
    )
    manager._active = {
        "period_started_at": "2026-09-12T22:00:00+00:00",
        "controls": {},
    }

    async def restore():
        manager._active = None

    manager._async_restore_locked = AsyncMock(side_effect=restore)
    manager._async_save_locked = AsyncMock()
    monkeypatch.setattr(quiet, "discover_satellite_capabilities", lambda *_args: [])

    await manager.async_reconcile(
        now=datetime(2026, 9, 13, 23, tzinfo=UTC)
    )

    manager._async_restore_locked.assert_awaited_once_with()
    assert manager._active is not None
    assert manager._active["period_started_at"].startswith("2026-09-13T22:00")


async def test_apply_volume_claims_only_when_needed_and_releases_on_failure() -> None:
    hass = _hass(
        {
            "media_player.loud": _state(
                "media_player.loud", "idle", volume_level=0.8
            ),
            "media_player.quiet": _state(
                "media_player.quiet", "idle", volume_level=0.1
            ),
        }
    )
    manager = _manager(hass)
    manager._config = quiet.QuietHoursConfig(max_volume=0.2)
    manager._async_save_locked = AsyncMock()
    manager._async_set_volume = AsyncMock()
    controls = {}

    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.quiet", controls
    )
    assert controls == {}

    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    assert controls["media_player.loud"]["original_value"] == 0.8
    assert controls["media_player.loud"]["quiet_value"] == 0.2
    manager._async_set_volume.assert_awaited_once_with("media_player.loud", 0.2)

    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    manager._async_set_volume.assert_awaited_once()

    controls.clear()
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("service failed"))
    await manager._async_apply_volume_locked(
        "assist_satellite.bedroom", "media_player.loud", controls
    )
    assert controls == {}


async def test_apply_switch_claims_only_when_needed_and_releases_on_failure() -> None:
    hass = _hass(
        {
            "switch.off": _state("switch.off", "off"),
            "switch.on": _state("switch.on", "on"),
        }
    )
    manager = _manager(hass)
    manager._async_save_locked = AsyncMock()
    manager._async_set_switch = AsyncMock()
    controls = {}

    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.off", False, controls
    )
    assert controls == {}

    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.on", False, controls
    )
    assert controls["switch.on"]["original_value"] is True
    manager._async_set_switch.assert_awaited_once_with("switch.on", False)

    controls.clear()
    manager._async_set_switch = AsyncMock(side_effect=RuntimeError("service failed"))
    await manager._async_apply_switch_locked(
        "assist_satellite.bedroom", "switch.on", False, controls
    )
    assert controls == {}


async def test_restore_only_reverts_controls_still_at_quiet_value() -> None:
    hass = _hass(
        {
            "media_player.owned": _state(
                "media_player.owned", "idle", volume_level=0.2
            ),
            "media_player.changed": _state(
                "media_player.changed", "idle", volume_level=0.4
            ),
            "switch.owned": _state("switch.owned", "off"),
            "switch.changed": _state("switch.changed", "on"),
        }
    )
    manager = _manager(hass)
    manager._active = {
        "period_started_at": "2026-09-13T22:00:00+00:00",
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
        },
    }
    manager._async_set_volume = AsyncMock()
    manager._async_set_switch = AsyncMock()
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    manager._async_set_volume.assert_awaited_once_with("media_player.owned", 0.7)
    manager._async_set_switch.assert_awaited_once_with("switch.owned", True)
    assert manager._active is None
    manager._async_save_locked.assert_awaited_once_with()


async def test_restore_failure_does_not_leave_stale_ownership() -> None:
    hass = _hass(
        {
            "media_player.bedroom": _state(
                "media_player.bedroom", "idle", volume_level=0.2
            )
        }
    )
    manager = _manager(hass)
    manager._active = {
        "controls": {
            "media_player.bedroom": {
                "kind": "volume",
                "original_value": 0.8,
                "quiet_value": 0.2,
            }
        }
    }
    manager._async_set_volume = AsyncMock(side_effect=RuntimeError("offline"))
    manager._async_save_locked = AsyncMock()

    await manager._async_restore_locked()

    assert manager._active is None
    manager._async_save_locked.assert_awaited_once_with()


async def test_service_helpers_use_expected_home_assistant_calls() -> None:
    manager = _manager()

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
        call(
            "switch",
            "turn_on",
            {"entity_id": "switch.wake"},
            blocking=True,
        ),
        call(
            "switch",
            "turn_off",
            {"entity_id": "switch.wake"},
            blocking=True,
        ),
    ]


async def test_save_persists_config_and_active_state() -> None:
    manager = _manager()
    manager._config = quiet.QuietHoursConfig(enabled=True, max_volume=0.15)
    manager._active = {"controls": {}}

    await manager._async_save_locked()

    manager._store.async_save.assert_awaited_once_with(
        {
            "config": manager._config.as_dict(),
            "active": {"controls": {}},
        }
    )


def test_normalize_active_migrates_targets_and_discards_invalid_controls() -> None:
    manager = _manager()
    assert manager._normalize_active(None) is None
    assert (
        manager._normalize_active(
            {
                "period_started_at": "bad",
                "period_ends_at": "also-bad",
                "controls": {},
            }
        )
        is None
    )
    assert (
        manager._normalize_active(
            {
                "period_started_at": "2026-09-13T22:00:00+00:00",
                "period_ends_at": "2026-09-13T21:00:00+00:00",
                "controls": {},
            }
        )
        is None
    )
    assert (
        manager._normalize_active(
            {
                "period_started_at": "2026-09-13T22:00:00+00:00",
                "period_ends_at": "2026-09-14T07:00:00+00:00",
                "controls": "bad",
            }
        )
        is None
    )

    active = manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-14T07:00:00+00:00",
            "applied_at": "then",
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
    assert active["controls"]["media_player.bedroom"] == {
        "kind": "volume",
        "satellite_entity_id": "",
        "original_value": 0.8,
        "quiet_value": 0.2,
    }

    active = manager._normalize_active(
        {
            "period_started_at": "2026-09-13T22:00:00+00:00",
            "period_ends_at": "2026-09-14T07:00:00+00:00",
            "controls": {
                "switch.wake": {
                    "kind": "switch",
                    "satellite_entity_id": "assist_satellite.bedroom",
                    "original_value": True,
                    "quiet_value": False,
                },
                "media_player.bad": {
                    "kind": "volume",
                    "original_value": 2,
                    "quiet_value": 0.2,
                },
                12: {},
            },
        }
    )
    assert active is not None
    assert set(active["controls"]) == {"switch.wake"}


def test_publish_state_and_snapshot_report_current_period(monkeypatch) -> None:
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
    state = manager.hass.states.get(quiet._STATE_ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["period_ends_at"] == period.end.isoformat()

    monkeypatch.setattr(
        quiet.dt_util,
        "now",
        lambda: datetime(2026, 9, 13, 23, tzinfo=UTC),
    )
    manager.discovery_snapshot = Mock(return_value=[{"name": "Bedroom"}])
    manager._active = {"controls": {"media_player.bedroom": {}}}
    snapshot = manager.snapshot()
    assert snapshot["active"] is True
    assert snapshot["owned_controls"] == ["media_player.bedroom"]
    assert snapshot["satellites"] == [{"name": "Bedroom"}]


def test_reschedule_cancels_old_callbacks_and_registers_boundaries(monkeypatch) -> None:
    manager = _manager()
    old = Mock()
    manager._unsubscribers = [old]
    manager._config = quiet.QuietHoursConfig(enabled=False)
    manager._publish_state = Mock()

    manager._reschedule()
    old.assert_called_once_with()
    assert manager._unsubscribers == []
    manager._publish_state.assert_called_once_with(None)

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
    assert time_change.call_args_list[0].kwargs == {
        "hour": 22,
        "minute": 15,
        "second": 0,
    }
    assert time_change.call_args_list[1].kwargs == {
        "hour": 6,
        "minute": 45,
        "second": 0,
    }
    interval.assert_called_once_with(
        manager.hass,
        manager._handle_discovery_tick,
        quiet._DISCOVERY_INTERVAL,
    )


async def test_time_callbacks_delegate_to_reconcile() -> None:
    manager = _manager()
    manager.async_reconcile = AsyncMock()
    now = datetime(2026, 9, 13, 22, tzinfo=UTC)

    await manager._handle_time_transition(now)
    await manager._handle_discovery_tick(now)

    assert manager.async_reconcile.await_args_list == [
        call(now=now),
        call(now=now),
    ]


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
