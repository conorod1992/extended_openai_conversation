"""Real Home Assistant acceptance coverage for exposed attribute discovery."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant

from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _entry,
    _management_call,
    _setup_entry,
)


@pytest.mark.asyncio
async def test_configuration_catalog_uses_real_assist_exposure(
    hass: HomeAssistant,
    hass_ws_client: Any,
) -> None:
    """Assist-exposed HA states must reach the management attribute catalogue."""
    entry = _entry("Exposed Attribute Catalogue Acceptance")
    await _setup_entry(hass, entry)

    hass.states.async_set(
        "sensor.attribute_catalog_exposed",
        "21.5",
        {
            "friendly_name": "Exposed Attribute Sensor",
            "unit_of_measurement": "°C",
            "battery_level": 84,
        },
    )
    hass.states.async_set(
        "sensor.attribute_catalog_hidden",
        "99",
        {
            "friendly_name": "Hidden Attribute Sensor",
            "unit_of_measurement": "%",
            "battery_level": 12,
        },
    )

    async_expose_entity(
        hass,
        conversation.DOMAIN,
        "sensor.attribute_catalog_exposed",
        True,
    )
    async_expose_entity(
        hass,
        conversation.DOMAIN,
        "sensor.attribute_catalog_hidden",
        False,
    )

    client = await _admin_client(hass, hass_ws_client)
    base = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="get",
    )
    assert "exposed_attribute_catalog" not in base

    result = await _management_call(
        client,
        entry=entry,
        section="configuration",
        action="live_metadata",
        metadata_keys=["exposed_attribute_catalog"],
    )
    catalog = result["exposed_attribute_catalog"]
    by_entity_id = {item["entity_id"]: item for item in catalog["entities"]}

    assert "sensor.attribute_catalog_exposed" in by_entity_id
    assert "sensor.attribute_catalog_hidden" not in by_entity_id

    exposed = by_entity_id["sensor.attribute_catalog_exposed"]
    assert exposed["name"] == "Exposed Attribute Sensor"
    assert {"friendly_name", "unit_of_measurement", "battery_level"}.issubset(
        set(exposed["attributes"])
    )
    assert exposed["durable_selection_available"] is False
