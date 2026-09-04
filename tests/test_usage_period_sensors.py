"""Tests for Usage Today and Usage Month rollover refreshes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses import sensor as sensor_module
from custom_components.extended_openai_conversation_responses.sensor import (
    UsageMonthSensor,
    UsageTodaySensor,
)


class FakeUsage:
    """Small UsageManager stand-in for entity lifecycle tests."""

    def __init__(self) -> None:
        self.totals = SimpleNamespace(total_tokens=0)
        self.remove_listener = Mock()

    def async_add_listener(self, _listener):
        return self.remove_listener

    def as_dict(self) -> dict:
        return {}

    def today_summary(self) -> dict:
        return self._summary()

    def month_summary(self) -> dict:
        return self._summary()

    @staticmethod
    def _summary() -> dict:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "run_count": 0,
            "api_request_count": 0,
            "failed_request_count": 0,
            "average_tokens_per_completed_run": 0,
        }


@pytest.mark.parametrize("sensor_cls", [UsageTodaySensor, UsageMonthSensor])
async def test_period_usage_sensor_refreshes_at_local_midnight(
    hass, monkeypatch, sensor_cls
) -> None:
    """Period sensors publish a fresh state at midnight without a usage event."""
    usage = FakeUsage()
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        title="Jarvis",
        data={},
    )
    entity = sensor_cls(subentry, usage)
    entity.hass = hass
    entity.entity_id = f"sensor.test_{sensor_cls.__name__.lower()}"
    write_state = Mock()
    monkeypatch.setattr(entity, "async_write_ha_state", write_state)

    tracked: dict[str, object] = {}
    remove_midnight_listener = Mock()

    def track_time_change(
        tracked_hass, action, *, hour=None, minute=None, second=None
    ):
        tracked.update(
            {
                "hass": tracked_hass,
                "action": action,
                "hour": hour,
                "minute": minute,
                "second": second,
            }
        )
        return remove_midnight_listener

    monkeypatch.setattr(sensor_module, "async_track_time_change", track_time_change)

    await entity.async_added_to_hass()

    assert tracked["hass"] is hass
    assert (tracked["hour"], tracked["minute"], tracked["second"]) == (0, 0, 0)

    action = tracked["action"]
    assert callable(action)
    action(datetime(2026, 10, 1, tzinfo=UTC))
    write_state.assert_called_once_with()

    await entity.async_will_remove_from_hass()
    remove_midnight_listener.assert_called_once_with()
    usage.remove_listener.assert_called_once_with()
