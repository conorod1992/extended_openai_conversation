"""Energy disclosure is limited to complete, understood, readable entries."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.functions import native


def _preferences():
    return {
        "energy_sources": [
            {
                "type": "solar",
                "stat_energy_from": "sensor.solar",
                "name": "Roof",
                "config_entry_solar_forecast": ["forecast-id"],
            },
            {
                "type": "grid",
                "stat_energy_from": "sensor.grid",
                "stat_cost": "sensor.cost",
                "entity_energy_price": None,
                "number_energy_price": None,
                "cost_adjustment_day": 2,
                "name": "Private tariff",
            },
            {
                "type": "battery",
                "stat_energy_from": "sensor.discharge",
                "stat_energy_to": "sensor.charge",
                "stat_rate": "sensor.power",
                "power_config": {
                    "stat_rate_from": "sensor.power",
                    "stat_rate_to": "sensor.power_to",
                },
                "stat_soc": "sensor.soc",
                "capacity": 12,
                "name": "Private battery",
            },
        ],
        "device_consumption": [
            {
                "stat_consumption": "sensor.parent",
                "stat_rate": "sensor.parent_power",
                "name": "Private appliance",
            },
            {"stat_consumption": "sensor.child", "included_in_stat": "sensor.parent"},
            {
                "stat_consumption": "sensor.grandchild",
                "included_in_stat": "sensor.child",
            },
            {"stat_consumption": "sensor.kettle", "name": "Kettle"},
        ],
        "device_consumption_water": [{"stat_consumption": "sensor.water"}],
    }


@pytest.mark.parametrize("visibility", ["all", "mixed", "none"])
async def test_energy_filtered_copy_and_private_metadata(hass, monkeypatch, visibility):
    data = _preferences()
    original = deepcopy(data)
    allowed = native._energy_entity_ids(data)
    if visibility == "mixed":
        allowed -= {
            "sensor.cost",
            "sensor.power_to",
            "sensor.parent_power",
            "sensor.water",
        }
    elif visibility == "none":
        allowed = set()
    monkeypatch.setattr(
        native.energy,
        "async_get_manager",
        AsyncMock(return_value=SimpleNamespace(data=data)),
    )
    result = await native.NativeFunction().get_energy(
        hass, {}, {}, None, [{"entity_id": value} for value in allowed]
    )
    if visibility == "all":
        assert result == original
    elif visibility == "mixed":
        assert result == {
            "energy_sources": [data["energy_sources"][0]],
            "device_consumption": [data["device_consumption"][-1]],
            "device_consumption_water": [],
        }
        assert "Private" not in repr(result)
        assert "capacity" not in repr(result) and "cost_adjustment_day" not in repr(
            result
        )
    else:
        assert result == {key: [] for key in data}
    assert native._energy_entity_ids(result) <= allowed
    for source in result["energy_sources"]:
        native.energy.data.ENERGY_SOURCE_SCHEMA([source])
    for key in ("device_consumption", "device_consumption_water"):
        for device in result[key]:
            native.energy.data.DEVICE_CONSUMPTION_SCHEMA(device)
    assert data == original
    if result["energy_sources"]:
        result["energy_sources"][0]["config_entry_solar_forecast"].append("changed")
        assert data == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("stat_cost", "sensor.hidden"),
        ("entity_energy_price", "sensor.hidden"),
        ("stat_energy_from", "private:external_meter"),
        ("unexpected", {"entity_id": "sensor.hidden", "secret": "private metadata"}),
    ],
)
def test_unknown_or_unverifiable_source_is_omitted(field, value):
    source = {"type": "gas", "stat_energy_from": "sensor.gas", field: value}
    data = {"energy_sources": [source], "private_metadata": "do not disclose"}
    assert native._filtered_energy_preferences(data, {"sensor.gas"}) == {
        "energy_sources": [],
        "device_consumption": [],
    }


@pytest.mark.parametrize(
    "entry",
    [
        None,
        [],
        {},
        {"type": "future", "name": "secret"},
        {"type": "solar", "stat_energy_from": ["sensor.visible"]},
    ],
)
def test_unknown_structures_fail_conservatively(entry):
    assert native._filtered_energy_preferences(
        {"energy_sources": [entry]}, {"sensor.visible"}
    ) == {
        "energy_sources": [],
        "device_consumption": [],
    }
