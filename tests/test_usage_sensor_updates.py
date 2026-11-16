"""Usage sensor events preserve counters, errors and retention polling."""

import asyncio
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses.sensor import (
    LastResponseUsageSensor,
    UsageMonthSensor,
    UsageSensor,
    UsageTodaySensor,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from tests.test_sensor_coverage import _subentry
from tests.test_usage import FailingStorage, FakeStorage


@pytest.mark.parametrize(
    "sensor_type,poll",
    [
        (UsageSensor, False),
        (UsageTodaySensor, False),
        (UsageMonthSensor, False),
        (LastResponseUsageSensor, True),
    ],
)
def test_polling_policy_respects_dynamic_last_response_expiry(sensor_type, poll):
    assert sensor_type(_subentry(), Mock()).should_poll is poll


@pytest.mark.parametrize("failed_save", [False, True])
async def test_compatibility_counter_notifies_even_when_save_raises(failed_save):
    manager = UsageManager(FailingStorage() if failed_save else FakeStorage())
    await manager.async_initialize()
    observed = []
    remove = manager.async_add_listener(lambda: observed.append(manager.as_dict()))
    if failed_save:
        with pytest.raises(OSError, match="disk full"):
            await manager.async_record_conversation()
    else:
        await manager.async_record_conversation()
        assert manager._storage.data["conversation_count"] == 1
    assert manager.totals.conversation_count == 1
    assert len(observed) == 1
    assert observed[0]["conversation_count"] == 1
    remove()


async def test_compatibility_counter_preserves_cancellation_and_notifies():
    class CancelledStorage(FakeStorage):
        async def async_save(self, _data):
            raise asyncio.CancelledError

    manager = UsageManager(CancelledStorage())
    await manager.async_initialize()
    listener = Mock()
    manager.async_add_listener(listener)
    with pytest.raises(asyncio.CancelledError):
        await manager.async_record_conversation()
    listener.assert_called_once_with()
    assert manager.totals.conversation_count == 1
