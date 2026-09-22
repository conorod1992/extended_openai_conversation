"""Quiet Hours configuration, scheduling boundaries, timezone and discovery contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    _config_from_data,
    discover_satellite_capabilities,
    quiet_period_for,
)

_DUBLIN = ZoneInfo("Europe/Dublin")


def _discovery_entry(
    entity_id: str,
    domain: str,
    device_id: str,
    *,
    platform: str = "esphome",
    original_name: str | None = None,
):
    return SimpleNamespace(
        entity_id=entity_id,
        domain=domain,
        device_id=device_id,
        disabled_by=None,
        platform=platform,
        original_name=original_name,
        name=None,
    )


def _discovery_hass(monkeypatch):
    entries = {
        "assist_satellite.bedroom": _discovery_entry(
            "assist_satellite.bedroom",
            "assist_satellite",
            "device-bedroom",
            original_name="Assist satellite",
        ),
        "media_player.bedroom": _discovery_entry(
            "media_player.bedroom",
            "media_player",
            "device-bedroom",
            original_name="Media Player",
        ),
        "switch.bedroom_wake_sound": _discovery_entry(
            "switch.bedroom_wake_sound",
            "switch",
            "device-bedroom",
            original_name="Wake sound",
        ),
    }
    registry = SimpleNamespace(async_get=entries.get)
    monkeypatch.setattr(runtime.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        runtime.er,
        "async_entries_for_device",
        lambda _registry, device_id: [
            entry for entry in entries.values() if entry.device_id == device_id
        ],
    )
    states = {
        "assist_satellite.bedroom": SimpleNamespace(
            entity_id="assist_satellite.bedroom",
            state="idle",
            attributes={"friendly_name": "Bedroom Voice"},
        ),
        "media_player.bedroom": SimpleNamespace(
            entity_id="media_player.bedroom",
            state="idle",
            attributes={"volume_level": 0.55},
        ),
        "switch.bedroom_wake_sound": SimpleNamespace(
            entity_id="switch.bedroom_wake_sound", state="on", attributes={}
        ),
    }
    state_machine = SimpleNamespace(
        get=states.get,
        async_all=lambda domain=None: [
            state
            for entity_id, state in states.items()
            if domain is None or entity_id.startswith(f"{domain}.")
        ],
    )
    return SimpleNamespace(states=state_machine)


def test_quiet_period_for_overnight_window() -> None:
    before_midnight = quiet_period_for(
        datetime(2026, 9, 11, 23, 30, tzinfo=UTC), "22:00", "07:00"
    )
    after_midnight = quiet_period_for(
        datetime(2026, 9, 12, 2, 30, tzinfo=UTC), "22:00", "07:00"
    )

    assert before_midnight is not None
    assert after_midnight is not None
    assert before_midnight.start == datetime(2026, 9, 11, 22, 0, tzinfo=UTC)
    assert after_midnight.start == before_midnight.start
    assert after_midnight.end == datetime(2026, 9, 12, 7, 0, tzinfo=UTC)
    assert (
        quiet_period_for(datetime(2026, 9, 12, 7, 0, tzinfo=UTC), "22:00", "07:00")
        is None
    )


def test_quiet_period_for_same_day_window_uses_start_inclusive_end_exclusive() -> None:
    assert (
        quiet_period_for(datetime(2026, 9, 12, 12, 59, tzinfo=UTC), "13:00", "15:00")
        is None
    )

    at_start = quiet_period_for(
        datetime(2026, 9, 12, 13, 0, tzinfo=UTC), "13:00", "15:00"
    )
    assert at_start is not None
    assert at_start.start == datetime(2026, 9, 12, 13, 0, tzinfo=UTC)
    assert at_start.end == datetime(2026, 9, 12, 15, 0, tzinfo=UTC)

    assert (
        quiet_period_for(datetime(2026, 9, 12, 15, 0, tzinfo=UTC), "13:00", "15:00")
        is None
    )


def test_quiet_period_for_dublin_spring_forward_keeps_local_wall_clock_window() -> None:
    period = quiet_period_for(
        datetime(2026, 3, 29, 3, 30, tzinfo=_DUBLIN), "22:00", "07:00"
    )

    assert period is not None
    assert period.start == datetime(2026, 3, 28, 22, 0, tzinfo=_DUBLIN)
    assert period.end == datetime(2026, 3, 29, 7, 0, tzinfo=_DUBLIN)
    assert period.start.utcoffset() == timedelta(0)
    assert period.end.utcoffset() == timedelta(hours=1)
    assert period.end.astimezone(UTC) - period.start.astimezone(UTC) == timedelta(
        hours=8
    )


def test_quiet_period_for_dublin_fall_back_keeps_local_wall_clock_window() -> None:
    period = quiet_period_for(
        datetime(2026, 10, 25, 3, 30, tzinfo=_DUBLIN), "22:00", "07:00"
    )

    assert period is not None
    assert period.start == datetime(2026, 10, 24, 22, 0, tzinfo=_DUBLIN)
    assert period.end == datetime(2026, 10, 25, 7, 0, tzinfo=_DUBLIN)
    assert period.start.utcoffset() == timedelta(hours=1)
    assert period.end.utcoffset() == timedelta(0)
    assert period.end.astimezone(UTC) - period.start.astimezone(UTC) == timedelta(
        hours=10
    )


def test_config_uses_ceiling_tri_state_and_migrates_prototype_names() -> None:
    config = _config_from_data(
        {
            "enabled": True,
            "start": "22:00",
            "end": "07:00",
            "max_volume": 0.2,
            "wake_sound": "off",
        }
    )
    assert config.max_volume == 0.2
    assert config.wake_sound == "off"

    migrated = _config_from_data({"volume_level": 0.3, "wake_sound_enabled": True})
    assert migrated.max_volume == 0.3
    assert migrated.wake_sound == "on"

    with pytest.raises(ValueError, match="between 0 and 1"):
        _config_from_data({"max_volume": 1.2})
    with pytest.raises(ValueError, match="on, off, or unchanged"):
        _config_from_data({"wake_sound": "sometimes"})
    with pytest.raises(ValueError, match="assist_satellite"):
        _config_from_data({"overrides": {"sensor.bad": {}}})
    with pytest.raises(ValueError, match="media_player"):
        _config_from_data(
            {
                "overrides": {
                    "assist_satellite.bedroom": {
                        "media_player_entity_id": "light.wrong"
                    }
                }
            }
        )


def test_discovery_uses_same_device_and_voice_pe_wake_sound(monkeypatch) -> None:
    hass = _discovery_hass(monkeypatch)

    discovered = discover_satellite_capabilities(hass, _config_from_data({}))

    assert len(discovered) == 1
    assert discovered[0].satellite_entity_id == "assist_satellite.bedroom"
    assert discovered[0].name == "Bedroom Voice"
    assert discovered[0].media_player_entity_id == "media_player.bedroom"
    assert discovered[0].media_player_source == "auto"
    assert discovered[0].media_player_candidates == ("media_player.bedroom",)
    assert discovered[0].wake_sound_entity_id == "switch.bedroom_wake_sound"
    assert discovered[0].wake_sound_source == "auto"
    assert discovered[0].wake_sound_candidates == ("switch.bedroom_wake_sound",)


def test_manual_mapping_overrides_auto_discovery(monkeypatch) -> None:
    hass = _discovery_hass(monkeypatch)
    config = _config_from_data(
        {
            "overrides": {
                "assist_satellite.bedroom": {
                    "media_player_entity_id": "media_player.manual",
                    "wake_sound_entity_id": "switch.manual_wake",
                }
            }
        }
    )

    discovered = discover_satellite_capabilities(hass, config)

    assert discovered[0].media_player_entity_id == "media_player.manual"
    assert discovered[0].media_player_source == "manual"
    assert discovered[0].wake_sound_entity_id == "switch.manual_wake"
    assert discovered[0].wake_sound_source == "manual"


def test_clock_config_and_value_object_edge_cases() -> None:
    override = runtime.SatelliteOverride(
        "assist_satellite.bedroom",
        "media_player.bedroom",
        "switch.bedroom_wake",
    )
    config = runtime.QuietHoursConfig(overrides=(override,))
    assert config.as_dict()["overrides"]["assist_satellite.bedroom"] == {
        "media_player_entity_id": "media_player.bedroom",
        "wake_sound_entity_id": "switch.bedroom_wake",
    }

    capability = runtime.SatelliteCapabilities(
        "assist_satellite.bedroom",
        "Bedroom",
        "device-1",
        "media_player.bedroom",
        "switch.bedroom_wake",
        "auto",
        "manual",
    )
    assert capability.as_dict()["wake_sound_source"] == "manual"
    assert capability.as_dict()["media_player_candidates"] == []
    assert capability.as_dict()["wake_sound_candidates"] == []

    with pytest.raises(ValueError, match="HH:MM string"):
        runtime._parse_clock(2200)
    with pytest.raises(ValueError, match="HH:MM"):
        runtime._parse_clock("22:00:01")
    with pytest.raises(ValueError, match="timezone-aware"):
        runtime.quiet_period_for(datetime(2026, 9, 13, 23), "22:00", "07:00")
    with pytest.raises(ValueError, match="must differ"):
        runtime.quiet_period_for(
            datetime(2026, 9, 13, 23, tzinfo=UTC), "22:00", "22:00"
        )

    assert (
        runtime.quiet_period_for(
            datetime(2026, 9, 13, 12, tzinfo=UTC), "09:00", "17:00"
        )
        is not None
    )
    assert (
        runtime.quiet_period_for(
            datetime(2026, 9, 13, 18, tzinfo=UTC), "09:00", "17:00"
        )
        is None
    )
    assert (
        runtime.quiet_period_for(
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
        ({"overrides": {"assist_satellite.bedroom": "bad"}}, "must be an object"),
        (
            {
                "overrides": {
                    "assist_satellite.bedroom": {"wake_sound_entity_id": "light.wrong"}
                }
            },
            "must be a switch entity",
        ),
    ],
)
def test_config_validation_rejects_bad_persisted_values(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        runtime._config_from_data(value)


def test_config_migrates_legacy_overrides_and_wake_flag() -> None:
    config = runtime._config_from_data(
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
        runtime.SatelliteOverride(
            "assist_satellite.bedroom",
            "media_player.bedroom",
            None,
        ),
    )
    assert runtime._optional_entity("", "switch", "Wake") is None


def test_parse_clock_rejects_invalid_iso_time() -> None:
    with pytest.raises(ValueError, match="time must use HH:MM"):
        runtime._parse_clock("not-a-time")


def test_config_rejects_more_than_100_overrides() -> None:
    overrides = {f"assist_satellite.room_{index}": {} for index in range(101)}

    with pytest.raises(ValueError, match="Select no more than 100 satellite overrides"):
        runtime._config_from_data({"overrides": overrides})
