"""Real-storage restart coverage for durable delayed Function Tools."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.core import Context, HomeAssistant

from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
    _EXECUTING,
)


def _entity() -> SimpleNamespace:
    """Return the minimal entity identity persisted with a delayed call."""
    return SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-1"),
    )


def _context() -> SimpleNamespace:
    """Return caller metadata that must survive restart recovery."""
    return SimpleNamespace(
        context=Context(user_id="user-1"),
        device_id="voice-kitchen",
    )


async def _setup_without_waiters(
    hass: HomeAssistant, monkeypatch
) -> tuple[DelayedToolManager, MagicMock]:
    """Set up a real-store manager while replacing only timer arming."""
    manager = DelayedToolManager(hass)
    arm = MagicMock()
    monkeypatch.setattr(manager, "_arm", arm)
    await manager.async_setup()
    return manager, arm


async def test_pending_call_and_retry_state_survive_real_store_restart(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A fresh manager reconstructs the full pending call and arms it once."""
    first, first_arm = await _setup_without_waiters(hass, monkeypatch)
    record = await first.async_schedule(
        _entity(),
        "control_light",
        {"delay": {"hours": 1}, "entity_id": "light.kitchen"},
        _context(),
    )
    first_arm.assert_called_once_with(record.call_id)

    assert await first._async_retry_agent(record) is True
    retried = first._records[record.call_id]
    assert retried.retry_count == 1
    first._handle_stop()

    second, second_arm = await _setup_without_waiters(hass, monkeypatch)

    assert second._records == {record.call_id: retried}
    second_arm.assert_called_once_with(record.call_id)
    recovered = second._records[record.call_id]
    assert recovered.arguments == {
        "delay": {"hours": 1},
        "entity_id": "light.kitchen",
    }
    assert recovered.user_id == "user-1"
    assert recovered.device_id == "voice-kitchen"
    assert recovered.retry_count == 1
    assert await second._store.async_load() == {"calls": [retried.as_dict()]}
    second._handle_stop()


async def test_executing_tombstone_is_durably_removed_on_restart(
    hass: HomeAssistant, monkeypatch
) -> None:
    """An indeterminate executing call is cleaned once and cannot replay later."""
    first, _first_arm = await _setup_without_waiters(hass, monkeypatch)
    record = await first.async_schedule(
        _entity(),
        "control_light",
        {"delay": {"hours": 1}, "entity_id": "light.kitchen"},
        _context(),
    )
    executing = replace(record, status=_EXECUTING)
    await first._async_replace_record(executing)
    assert await first._store.async_load() == {"calls": [executing.as_dict()]}
    first._handle_stop()

    second, second_arm = await _setup_without_waiters(hass, monkeypatch)

    assert second._records == {}
    second_arm.assert_not_called()
    assert await second._store.async_load() == {"calls": []}
    second._handle_stop()

    third, third_arm = await _setup_without_waiters(hass, monkeypatch)

    assert third._records == {}
    third_arm.assert_not_called()
    assert await third._store.async_load() == {"calls": []}
    third._handle_stop()
