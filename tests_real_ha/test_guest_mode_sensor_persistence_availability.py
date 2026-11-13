"""Exercise Guest Mode sensor unavailability through native HA state writes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import guest_mode
from homeassistant.components import sensor as ha_sensor
from homeassistant.exceptions import HomeAssistantError
from tests_real_ha.test_acceptance_lifecycle import _make_entry
from tests_real_ha.test_management_backend_acceptance import _setup_entry


async def test_real_ha_guest_sensor_marks_uncertain_storage_unavailable_then_recovers(
    hass, caplog
):
    entry = _make_entry("Guest sensor persistence", include_ai_task=False)
    entry.add_to_hass(hass)
    await _setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    manager = await guest_mode.async_get_guest_mode(
        hass, entry.entry_id, subentry.subentry_id
    )
    entity = next(
        entity
        for entity in hass.data[ha_sensor.DATA_COMPONENT].entities
        if entity.unique_id == f"{subentry.subentry_id}_guest_mode"
    )
    await manager.async_update_trusted(indefinite=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity.entity_id).state == "active_indefinitely"
    assert len(manager._listeners) == 1

    original = manager._store
    try:
        manager._store = SimpleNamespace(
            async_save=AsyncMock(side_effect=OSError("save unreachable")),
            async_load=AsyncMock(side_effect=OSError("readback unreachable")),
        )
        with pytest.raises(OSError, match="save unreachable"):
            await manager.async_disable_trusted()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(entity.entity_id).state == "unavailable"
        state = hass.states.get(entity.entity_id)
        assert "active_from" not in state.attributes
        assert "currently_active" not in state.attributes
        assert "active_until" not in state.attributes
        assert not entity.available
        with pytest.raises(HomeAssistantError, match="persistence is unavailable"):
            manager.status()
        before_errors = len(caplog.records)
        for _ in range(25):
            entity.available
            assert entity.native_value is None
            assert entity.extra_state_attributes == {}
        await hass.async_block_till_done(wait_background_tasks=True)
        assert len(caplog.records) == before_errors
    finally:
        manager._store = original

    await manager.async_initialize()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entity.available
    assert hass.states.get(entity.entity_id).state == "active_indefinitely"
    assert hass.states.get(entity.entity_id).attributes["currently_active"] is True
    assert len(manager._listeners) == 1

    # Normal platform unload must remove the manager's owned callback.
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not manager._listeners
