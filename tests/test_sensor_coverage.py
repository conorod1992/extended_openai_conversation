"""Focused coverage for diagnostic sensor setup and lifecycle behavior."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import sensor as sensor_module
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
)
from custom_components.extended_openai_conversation_responses.sensor import (
    GuestModeSensor,
    LastResponseUsageSensor,
    UsageMonthSensor,
    UsageSensor,
    UsageTodaySensor,
    async_setup_entry,
)


SUMMARY = {
    "total_tokens": 321,
    "input_tokens": 200,
    "output_tokens": 100,
    "cached_input_tokens": 15,
    "reasoning_tokens": 6,
    "run_count": 4,
    "api_request_count": 5,
    "failed_request_count": 1,
    "average_tokens_per_completed_run": 80.25,
}


class FakeUsage:
    """Small observable stand-in for UsageManager."""

    def __init__(self) -> None:
        self.totals = SimpleNamespace(total_tokens=777)
        self.latest_run = None
        self.request_retention_days = None
        self.run_retention_days = None
        self.listeners = []

    def as_dict(self) -> dict:
        return {"total_tokens": 777, "api_request_count": 9}

    def today_summary(self) -> dict:
        return {**SUMMARY, "total_tokens": 111}

    def month_summary(self) -> dict:
        return {**SUMMARY, "total_tokens": 222}

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return Mock(name="remove_usage_listener")


class FakeGuestMode:
    """Small observable stand-in for GuestModeManager."""

    def __init__(self) -> None:
        self.listeners = []
        self.current_status = {
            "state": "scheduled",
            "active_from": "2026-09-13T20:00:00+01:00",
            "active_until": "2026-09-14T08:00:00+01:00",
            "indefinite": False,
            "currently_active": False,
            "scheduled": True,
        }

    def status(self) -> dict:
        return dict(self.current_status)

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return Mock(name="remove_guest_listener")


def _subentry(
    subentry_id: str = "agent-1",
    *,
    subentry_type: str = "conversation",
    data: dict | None = None,
):
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type=subentry_type,
        title="Kitchen assistant",
        data={} if data is None else data,
    )


async def _noop_added_to_hass(_entity) -> None:
    """Replace Home Assistant's platform-owned base lifecycle in unit tests."""


async def test_setup_entry_adds_five_sensors_and_applies_retention_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = FakeUsage()
    guest_mode = FakeGuestMode()
    usage_getter = AsyncMock(return_value=usage)
    guest_getter = AsyncMock(return_value=guest_mode)
    monkeypatch.setattr(sensor_module, "async_get_usage", usage_getter)
    monkeypatch.setattr(sensor_module, "async_get_guest_mode", guest_getter)

    conversation = _subentry(
        data={
            CONF_CHAT_MODEL: "gpt-test",
            CONF_USAGE_REQUEST_RETENTION_DAYS: "17",
            CONF_USAGE_RUN_RETENTION_DAYS: "23",
        }
    )
    ignored = _subentry("not-an-agent", subentry_type="other")
    entry = SimpleNamespace(
        entry_id="entry-1",
        subentries={
            conversation.subentry_id: conversation,
            ignored.subentry_id: ignored,
        },
    )
    additions = []

    def add_entities(entities, *, config_subentry_id):
        additions.append((entities, config_subentry_id))

    hass = object()
    await async_setup_entry(hass, entry, add_entities)

    usage_getter.assert_awaited_once_with(hass, "entry-1", "agent-1")
    guest_getter.assert_awaited_once_with(hass, "entry-1", "agent-1")
    assert usage.request_retention_days == 17
    assert usage.run_retention_days == 23
    assert len(additions) == 1
    entities, subentry_id = additions[0]
    assert subentry_id == "agent-1"
    assert [type(entity) for entity in entities] == [
        UsageSensor,
        UsageTodaySensor,
        UsageMonthSensor,
        LastResponseUsageSensor,
        GuestModeSensor,
    ]
    assert all(entity.device_info["model"] == "gpt-test" for entity in entities)


async def test_setup_entry_uses_default_retention_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = FakeUsage()
    monkeypatch.setattr(
        sensor_module, "async_get_usage", AsyncMock(return_value=usage)
    )
    monkeypatch.setattr(
        sensor_module,
        "async_get_guest_mode",
        AsyncMock(return_value=FakeGuestMode()),
    )
    subentry = _subentry()
    entry = SimpleNamespace(entry_id="entry-1", subentries={"agent-1": subentry})

    await async_setup_entry(object(), entry, Mock())

    assert usage.request_retention_days == DEFAULT_USAGE_REQUEST_RETENTION_DAYS
    assert usage.run_retention_days == DEFAULT_USAGE_RUN_RETENTION_DAYS


def test_guest_mode_sensor_exposes_status_and_default_device_model() -> None:
    guest_mode = FakeGuestMode()
    sensor = GuestModeSensor(_subentry(), guest_mode)

    assert sensor.unique_id == "agent-1_guest_mode"
    assert sensor.device_info["model"] == DEFAULT_CHAT_MODEL
    assert sensor.native_value == "scheduled"
    assert sensor.extra_state_attributes == {
        "active_from": "2026-09-13T20:00:00+01:00",
        "active_until": "2026-09-14T08:00:00+01:00",
        "indefinite": False,
        "currently_active": False,
        "scheduled": True,
    }


async def test_guest_mode_sensor_registers_manager_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sensor_module.SensorEntity, "async_added_to_hass", _noop_added_to_hass
    )
    guest_mode = FakeGuestMode()
    sensor = GuestModeSensor(_subentry(), guest_mode)

    await sensor.async_added_to_hass()

    assert len(guest_mode.listeners) == 1
    assert guest_mode.listeners[0].__self__ is sensor
    assert guest_mode.listeners[0].__name__ == "async_write_ha_state"


async def test_usage_sensor_exposes_totals_attributes_and_registers_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sensor_module.SensorEntity, "async_added_to_hass", _noop_added_to_hass
    )
    usage = FakeUsage()
    sensor = UsageSensor(_subentry(data={CONF_CHAT_MODEL: "gpt-test"}), usage)

    assert sensor.unique_id == "agent-1_usage"
    assert sensor.native_value == 777
    assert sensor.extra_state_attributes == {
        "total_tokens": 777,
        "api_request_count": 9,
    }
    assert sensor.device_info["model"] == "gpt-test"

    await sensor.async_added_to_hass()

    assert len(usage.listeners) == 1
    assert usage.listeners[0].__self__ is sensor
    assert usage.listeners[0].__name__ == "async_write_ha_state"


def test_period_sensors_use_distinct_summaries_and_attributes() -> None:
    usage = FakeUsage()
    today = UsageTodaySensor(_subentry(), usage)
    month = UsageMonthSensor(_subentry(), usage)

    assert today.unique_id == "agent-1_usage_today"
    assert month.unique_id == "agent-1_usage_month"
    assert today.native_value == 111
    assert month.native_value == 222
    assert today.extra_state_attributes == {
        "input": 200,
        "output": 100,
        "cached_input": 15,
        "reasoning": 6,
        "runs": 4,
        "requests": 5,
        "failures": 1,
        "average_tokens_per_run": 80.25,
    }


async def test_period_sensor_tracks_midnight_and_rollover_writes_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sensor_module.SensorEntity, "async_added_to_hass", _noop_added_to_hass
    )
    tracked = {}
    remove_time_listener = Mock(name="remove_time_listener")

    def track_time_change(hass, action, **kwargs):
        tracked.update(hass=hass, action=action, kwargs=kwargs)
        return remove_time_listener

    monkeypatch.setattr(sensor_module, "async_track_time_change", track_time_change)
    usage = FakeUsage()
    sensor = UsageTodaySensor(_subentry(), usage)
    hass = SimpleNamespace()
    sensor.hass = hass
    write_state = Mock()
    monkeypatch.setattr(sensor, "async_write_ha_state", write_state)

    await sensor.async_added_to_hass()

    assert tracked == {
        "hass": hass,
        "action": sensor._handle_period_rollover,
        "kwargs": {"hour": 0, "minute": 0, "second": 0},
    }
    tracked["action"](datetime(2026, 9, 14, 0, 0, 0))
    write_state.assert_called_once_with()


def test_last_response_sensor_handles_absent_and_completed_run() -> None:
    usage = FakeUsage()
    sensor = LastResponseUsageSensor(_subentry(), usage)

    assert sensor.unique_id == "agent-1_last_response_usage"
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}

    usage.latest_run = SimpleNamespace(
        completed_at="2026-09-13T12:00:00+00:00",
        duration_ms=450,
        models=["gpt-test"],
        providers=["openai"],
        request_count=2,
        tool_call_count=3,
        input_tokens=120,
        output_tokens=80,
        cached_input_tokens=20,
        reasoning_tokens=10,
        total_tokens=210,
        successful=False,
        error_type="provider_error",
    )

    assert sensor.native_value == 210
    assert sensor.extra_state_attributes == {
        "completed_at": "2026-09-13T12:00:00+00:00",
        "duration_ms": 450,
        "models": ["gpt-test"],
        "providers": ["openai"],
        "api_request_count": 2,
        "tool_call_count": 3,
        "input": 120,
        "output": 80,
        "cached_input": 20,
        "reasoning": 10,
        "success": False,
        "error_type": "provider_error",
    }
