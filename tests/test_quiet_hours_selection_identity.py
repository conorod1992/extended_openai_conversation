"""Manual-selection persistence is shared by the core and public runtimes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    quiet_hours as public,
)
from custom_components.extended_openai_conversation_responses import (
    quiet_hours_runtime as runtime,
)
from homeassistant.exceptions import HomeAssistantError


def _manager(hass, monkeypatch, manager_type=runtime.QuietHoursManager):
    manager = manager_type(hass)
    manager._config = runtime._config_from_data(
        {
            "overrides": {
                "assist_satellite.selected": {
                    "media_player_entity_id": "media_player.selected",
                    "wake_sound_entity_id": "switch.selected",
                }
            }
        }
    )
    entries = {
        entity_id: SimpleNamespace(
            domain=entity_id.split(".")[0], platform="test", unique_id=entity_id
        )
        for entity_id in manager._selected_entity_ids()
    }
    lookup = {key: key for key in entries}
    registry = SimpleNamespace(
        async_get=entries.get,
        async_get_entity_id=lambda domain, platform, unique: lookup.get(unique),
    )
    monkeypatch.setattr(runtime.er, "async_get", lambda _h: registry)
    manager._store = SimpleNamespace(async_save=AsyncMock(), async_load=AsyncMock())
    return manager, entries, lookup


@pytest.mark.parametrize(
    "manager_type", [runtime.QuietHoursManager, public.QuietHoursManager]
)
async def test_selection_bindings_are_detached_persisted_and_follow_all_renames(
    hass, monkeypatch, manager_type
):
    manager, entries, lookup = _manager(hass, monkeypatch, manager_type)
    assert manager._migrate_selected_entity_ids()
    assert not manager._migrate_selected_entity_ids()
    initial = deepcopy(manager._selection_identities)
    assert manager._normalize_selection_identities(initial, manager._config) == initial
    manager._active = {
        "controls": {
            "media_player.selected": {
                "registry_identity": initial["media_player.selected"],
                "kind": "volume",
                "satellite_entity_id": "assist_satellite.selected",
                "original_value": 0.8,
                "quiet_value": 0.2,
            }
        },
        "observed_controls": [],
    }
    for key in lookup:
        lookup[key] = key.replace("selected", "renamed")
    assert await manager._async_resolve_control_identities_locked()
    saved = manager._store.async_save.await_args.args[0]
    assert saved["config"]["overrides"] == {
        "assist_satellite.renamed": {
            "media_player_entity_id": "media_player.renamed",
            "wake_sound_entity_id": "switch.renamed",
        }
    }
    assert saved["selection_identities"] == {
        lookup[key]: identity for key, identity in initial.items()
    }
    assert "media_player.renamed" in saved["active"]["controls"]
    assert "media_player.selected" not in saved["active"]["controls"]
    manager._config = runtime.QuietHoursConfig()
    assert manager._migrate_selected_entity_ids()
    assert not manager._selection_identities
    assert not manager._migrate_selected_entity_ids()


@pytest.mark.parametrize(
    "value",
    [
        [],
        "invalid",
        {"switch.selected": []},
        {"switch.selected": ["media_player", "test", "id"]},
        {"switch.selected": ["switch", "", "id"]},
        {"switch.selected": ["switch", "test", 1]},
    ],
)
def test_invalid_selected_bindings_are_rejected(hass, monkeypatch, value):
    manager, _, _ = _manager(hass, monkeypatch)
    with pytest.raises(ValueError, match="selection identit"):
        manager._normalize_selection_identities(value, manager._config)
    assert manager._normalize_selection_identities(None, manager._config) == {}
    assert (
        manager._normalize_selection_identities(
            {"switch.unselected": []}, manager._config
        )
        == {}
    )


@pytest.mark.parametrize("removed", ["assist_satellite", "media_player", "switch"])
def test_removed_identity_never_falls_back_to_reused_id_or_another_control(
    hass, monkeypatch, removed
):
    manager, _, lookup = _manager(hass, monkeypatch)
    manager._migrate_selected_entity_ids()
    lookup[f"{removed}.selected"] = None
    manual = runtime.SatelliteCapabilities(
        "assist_satellite.selected",
        "Selected",
        None,
        "media_player.selected",
        "switch.selected",
        "manual",
        "manual",
    )
    auto = runtime.SatelliteCapabilities(
        "assist_satellite.other",
        "Other",
        None,
        "media_player.other",
        "switch.other",
        "auto",
        "auto",
    )
    result = manager._filter_selected_capabilities([manual, auto])
    assert result[-1] == auto
    if removed == "assist_satellite":
        assert result == [auto]
    else:
        field = (
            "media_player_entity_id"
            if removed == "media_player"
            else "wake_sound_entity_id"
        )
        assert getattr(result[0], field) is None
        assert result[0].satellite_entity_id == manual.satellite_entity_id


async def test_continuous_selection_churn_defers_actions_after_bounded_persistence(
    hass, monkeypatch
):
    manager, _, lookup = _manager(hass, monkeypatch)
    manager._migrate_selected_entity_ids()

    async def save(_data):
        for key in lookup:
            lookup[key] += "x"

    manager._store.async_save.side_effect = save
    for key in lookup:
        lookup[key] += "x"
    assert not await manager._async_resolve_control_identities_locked()
    assert manager._store.async_save.await_count == 8
    hass.services.async_call.assert_not_awaited()


async def test_selection_change_after_discovery_prevents_stale_device_command(
    hass, monkeypatch
):
    manager, _, lookup = _manager(hass, monkeypatch)
    manager._migrate_selected_entity_ids()
    lookup["assist_satellite.selected"] = "assist_satellite.renamed"
    with pytest.raises(HomeAssistantError, match="selection changed"):
        await manager._async_set_volume("media_player.selected", 0.8)
    hass.services.async_call.assert_not_awaited()
