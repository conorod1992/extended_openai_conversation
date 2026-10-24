"""HA consumes a one-shot listener before running its shutdown coroutine."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import usage
from tests.test_usage_detail_lifecycle import _manager


class OneShotBus:
    def __init__(self):
        self.listener = None
        self.removals = 0

    def async_listen_once(self, event_type, listener):
        self.listener = listener

        def remove():
            assert self.listener is listener, "stop listener already removed"
            self.removals += 1
            self.listener = None

        return remove

    async def fire(self):
        listener, self.listener = self.listener, None
        if listener is not None:
            self.removals += 1
            await listener(SimpleNamespace(event_type=usage.EVENT_HOMEASSISTANT_STOP))


@pytest.mark.parametrize("ending", ["event", "explicit", "dispose"])
async def test_maintenance_listener_is_released_once(monkeypatch, ending):
    manager = _manager(None)
    manager._async_save_aggregates = AsyncMock()
    manager._async_save_details = AsyncMock()
    bus = OneShotBus()
    cancel_timer = Mock()
    monkeypatch.setattr(
        usage, "async_track_time_interval", Mock(return_value=cancel_timer)
    )
    hass = SimpleNamespace(bus=bus)
    usage._register_usage_maintenance(hass, manager)
    usage._register_usage_maintenance(hass, manager)
    if ending == "event":
        await bus.fire()
    elif ending == "explicit":
        await manager.async_shutdown()
        await bus.fire()
    else:
        manager.dispose_maintenance()
        await bus.fire()
    manager.dispose_maintenance()
    assert bus.removals == 1
    cancel_timer.assert_called_once()
    assert manager._cancel_stop is None
    assert manager._cancel_retention is None
    if ending != "dispose":
        manager._async_save_aggregates.assert_awaited_once()
        manager._async_save_details.assert_awaited_once()
    else:
        manager._async_save_aggregates.assert_not_awaited()
        manager._async_save_details.assert_not_awaited()
