"""Additional persistence-state tests for durable delayed Function Tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
)
from tests.test_delayed_tools import _record


@pytest.mark.parametrize("invalidated_runtime", (False, True))
async def test_store_load_failure_quarantines_until_fresh_manager(
    hass, invalidated_runtime
) -> None:
    """Unreadable startup and invalidated queues require a fresh manager."""
    manager = DelayedToolManager(hass)
    store = SimpleNamespace(
        async_load=AsyncMock(
            side_effect=[OSError("storage unavailable"), {"calls": []}]
        ),
        async_save=AsyncMock(),
    )
    manager._store = store
    if invalidated_runtime:
        # After a failed transaction and readback, the manager can retry
        # initialization, but an unreadable *setup* load must quarantine it.
        pending = _record()
        manager._records[pending.call_id] = pending
        manager._setup_complete = True
        manager._started = True
        manager._invalidate_after_unreadable_store()
        assert not manager._setup_complete and not manager._started
        assert not manager._records and not manager._quarantined

    await manager.async_setup()
    assert manager._setup_complete is True
    assert manager._quarantined is True
    assert manager._records == {}
    await manager.async_setup()
    store.async_load.assert_awaited_once()
    store.async_save.assert_not_awaited()

    # After an administrator repairs the store and restarts Home Assistant,
    # a new manager may load the now-authoritative queue.
    restarted = DelayedToolManager(hass)
    restarted._store = store
    await restarted.async_setup()
    assert restarted._setup_complete is True
    assert restarted._quarantined is False
    assert restarted._records == {}
    assert store.async_load.await_count == 2


async def test_failed_cancellation_keeps_due_call_retryable(hass, monkeypatch) -> None:
    """A Store failure cannot orphan a forbidden call without a live retry path."""
    manager = DelayedToolManager(hass)
    record = _record()
    manager._records = {record.call_id: record}
    manager._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("storage unavailable")),
        async_load=AsyncMock(return_value={"calls": [record.as_dict()]}),
    )
    hass.config_entries.async_get_entry = MagicMock(
        return_value=SimpleNamespace(
            disabled_by=None,
            subentries={
                "agent": SimpleNamespace(subentry_type="conversation", data={})
            },
        )
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.delayed_tools.configured_function_tools_from_data",
        lambda _data: [
            {
                "enabled": False,
                "spec": {"name": "control_light"},
                "function": {"type": "native", "name": "execute_service_single"},
            }
        ],
    )

    assert await manager._async_execute_due(record.call_id) is True
    assert manager._records[record.call_id] == record
