"""Broadcast uses HA's real registry inheritance and retains older-HA behavior."""

from types import MappingProxyType, SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    IntercomManager,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
)


@pytest.mark.skipif(
    not hasattr(dr.DeviceRegistry, "async_get_or_create_child"),
    reason="HA release predates child devices",
)
async def test_broadcast_inherits_parent_area_floor_and_area_labels(hass, monkeypatch):
    """Only HA infrastructure is doubled; all four registries are genuine."""
    registries = [
        (dr, dr.DeviceRegistry(hass)),
        (er, er.EntityRegistry(hass)),
        (ar, ar.AreaRegistry(hass)),
        (fr, fr.FloorRegistry(hass)),
    ]
    for module, registry in registries:
        hass.data[module.DATA_REGISTRY] = registry
        await registry.async_load(load_empty=True)
        monkeypatch.setattr(registry, "async_schedule_save", lambda: None)
    hass.config_entries.async_get_entry.return_value = ConfigEntry(
        entry_id="entry",
        version=1,
        minor_version=1,
        domain="test",
        title="Satellite",
        data={},
        options={},
        source="user",
        unique_id=None,
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )
    devices, entities, areas, floors = [registry for _, registry in registries]
    floor = floors.async_create("Ground")
    kitchen = areas.async_create(
        "Kitchen", floor_id=floor.floor_id, labels={"announcements"}
    )
    bedroom = areas.async_create("Bedroom")
    parent = devices.async_get_or_create(
        config_entry_id="entry", identifiers={("test", "parent")}
    )
    devices.async_update_device(parent.id, area_id=kitchen.id)
    child = devices.async_get_or_create_child(
        config_entry_id="entry",
        identifiers={("test", "child")},
        parent_device_id=parent.id,
    )
    satellite = entities.async_get_or_create(
        "assist_satellite",
        "test",
        "child",
        device_id=child.id,
        original_name="Satellite",
    )
    hass.states.async_all.return_value = [
        SimpleNamespace(
            entity_id=satellite.entity_id,
            attributes={"supported_features": ANNOUNCE_FEATURE},
        )
    ]
    manager = IntercomManager(hass)

    def targets(**selection):
        return manager.resolve_targets(**selection)

    assert targets(area_ids=[kitchen.id]) == [satellite.entity_id]
    assert targets(floor_ids=[floor.floor_id]) == [satellite.entity_id]
    assert targets(label_ids=["announcements"]) == [satellite.entity_id]
    assert targets(area_ids=[bedroom.id]) == []
    devices.async_update_device(parent.id, area_id=bedroom.id)
    assert targets(area_ids=[kitchen.id]) == []
    assert targets(floor_ids=[floor.floor_id]) == []
    assert targets(label_ids=["announcements"]) == []
    assert targets(area_ids=[bedroom.id]) == [satellite.entity_id]
    devices.async_update_child_device(child.id, area_id=kitchen.id)
    assert targets(area_ids=[kitchen.id]) == [satellite.entity_id]
    entities.async_update_entity(satellite.entity_id, area_id=bedroom.id)
    assert targets(area_ids=[bedroom.id]) == [satellite.entity_id]
    assert targets(area_ids=[kitchen.id]) == []
    assert targets(entity_ids=[satellite.entity_id]) == [satellite.entity_id]
    assert targets(whole_home=True) == [satellite.entity_id]


def test_legacy_ha_without_effective_area_helper_retains_device_and_entity_overrides(
    hass, monkeypatch
):
    monkeypatch.delattr(dr, "async_get_effective_area_id", raising=False)
    entity = SimpleNamespace(area_id=None, device_id="device")
    monkeypatch.setattr(
        er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: entity)
    )
    monkeypatch.setattr(
        dr,
        "async_get",
        lambda _: SimpleNamespace(
            async_get=lambda _: SimpleNamespace(area_id="device-area")
        ),
    )
    manager = IntercomManager(hass)
    assert manager._entity_area_id("assist_satellite.test") == "device-area"
    entity.area_id = "entity-area"
    assert manager._entity_area_id("assist_satellite.test") == "entity-area"
