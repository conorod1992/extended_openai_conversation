"""Tests for Usage Today and Usage Month rollover refreshes."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, call
from zoneinfo import ZoneInfo

import pytest

from custom_components.extended_openai_conversation_responses import (
    sensor as sensor_module,
)
from custom_components.extended_openai_conversation_responses.sensor import (
    UsageMonthSensor,
    UsageTodaySensor,
)


class FakeUsage:
    """Small UsageManager stand-in for entity lifecycle tests."""

    def __init__(self) -> None:
        self.totals = SimpleNamespace(total_tokens=0)
        self.daily_total_tokens = 0
        self.lifetime_available = True
        self.daily_available = True
        self.remove_listener = Mock()

    def async_add_listener(self, _listener):
        return self.remove_listener

    def as_dict(self) -> dict:
        return {}

    def today_summary(self) -> dict:
        return self._summary()

    def month_summary(self, month=None) -> dict:
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
@pytest.mark.parametrize(
    ("zone_name", "previous", "boundary", "following"),
    [
        ("UTC", datetime(2026, 9, 30, 23, 59, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)),
        ("America/Santiago", datetime(2026, 9, 6, 3, 59, tzinfo=UTC), datetime(2026, 9, 6, 4, tzinfo=UTC), datetime(2026, 9, 7, 3, tzinfo=UTC)),
    ],
)
async def test_period_usage_sensor_refreshes_at_local_midnight(
    hass, monkeypatch, sensor_cls, zone_name, previous, boundary, following
) -> None:
    """Publish at the local date boundary, including a skipped midnight."""
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
    on_remove = Mock()
    monkeypatch.setattr(entity, "async_write_ha_state", write_state)
    monkeypatch.setattr(entity, "async_on_remove", on_remove)

    tracked: dict[str, object] = {}
    remove_midnight_listener = Mock()
    zone = ZoneInfo(zone_name)
    clock = Mock(return_value=previous.astimezone(zone))
    monkeypatch.setattr(sensor_module.dt_util, "now", clock)

    def track_point(tracked_hass, action, point):
        tracked.update(
            {
                "hass": tracked_hass,
                "action": action,
                "point": point,
            }
        )
        return remove_midnight_listener

    monkeypatch.setattr(sensor_module, "async_track_point_in_utc_time", track_point)

    with monkeypatch.context() as zone_patch:
        zone_patch.setattr(sensor_module.dt_util, "DEFAULT_TIME_ZONE", zone)
        await entity.async_added_to_hass()

        assert tracked["hass"] is hass
        assert tracked["point"] == boundary
        assert on_remove.call_args_list == [
            call(entity._remove_usage_listener),
            call(entity._remove_period_rollover),
            call(hass.bus.async_listen.return_value),
        ]
        assert hass.bus.async_listen.call_args.args == (
            sensor_module.EVENT_CORE_CONFIG_UPDATE,
            entity._handle_core_config_update,
        )
        assert entity._usage_listener_remove is usage.remove_listener

        action = tracked["action"]
        assert callable(action)
        clock.return_value = boundary.astimezone(zone)
        action(boundary)
        write_state.assert_called_once_with()
        assert tracked["point"] == following
        entity._remove_period_rollover()
        remove_midnight_listener.assert_called_once_with()
