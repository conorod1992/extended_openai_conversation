"""Only known restore quarantine may suppress a sensor initialization failure."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import sensor
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_sensor_coverage import FakeGuestMode, FakeUsage, _subentry


@pytest.mark.parametrize("manager", ["async_get_usage", "async_get_guest_mode"])
@pytest.mark.parametrize("quarantined", [False, True])
@pytest.mark.parametrize(
    "error_type", [HomeAssistantError, ValueError, asyncio.CancelledError]
)
async def test_sensor_setup_suppresses_only_quarantined_ha_errors(
    hass, monkeypatch, manager, quarantined, error_type
):
    sub = _subentry()
    entry = SimpleNamespace(entry_id="sensor-errors", subentries={sub.subentry_id: sub})
    get_agent_maintenance_gate(
        hass, entry.entry_id, sub.subentry_id
    ).recovery_required = quarantined
    monkeypatch.setattr(sensor, "async_get_usage", AsyncMock(return_value=FakeUsage()))
    monkeypatch.setattr(
        sensor, "async_get_guest_mode", AsyncMock(return_value=FakeGuestMode())
    )
    error = error_type("initialization failed")
    monkeypatch.setattr(sensor, manager, AsyncMock(side_effect=error))
    add = Mock()
    if quarantined and error_type is HomeAssistantError:
        await sensor.async_setup_entry(hass, entry, add)
    else:
        with pytest.raises(error_type) as raised:
            await sensor.async_setup_entry(hass, entry, add)
        assert raised.value is error
    add.assert_not_called()
