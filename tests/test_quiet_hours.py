"""Fast unit tests for Quiet Hours parsing and satellite discovery."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import quiet_hours_runtime
from custom_components.extended_openai_conversation_responses.quiet_hours import (
    _config_from_data,
    discover_satellite_capabilities,
    quiet_period_for,
)


def _entry(
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
        "assist_satellite.bedroom": _entry(
            "assist_satellite.bedroom",
            "assist_satellite",
            "device-bedroom",
            original_name="Assist satellite",
        ),
        "media_player.bedroom": _entry(
            "media_player.bedroom",
            "media_player",
            "device-bedroom",
            original_name="Media Player",
        ),
        "switch.bedroom_wake_sound": _entry(
            "switch.bedroom_wake_sound",
            "switch",
            "device-bedroom",
            original_name="Wake sound",
        ),
    }
    registry = SimpleNamespace(async_get=entries.get)
    monkeypatch.setattr(quiet_hours_runtime.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        quiet_hours_runtime.er,
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
    assert discovered[0].wake_sound_entity_id == "switch.bedroom_wake_sound"
    assert discovered[0].wake_sound_source == "auto"


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
