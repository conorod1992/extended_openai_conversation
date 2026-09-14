"""Regression coverage for Broadcast queue lifecycle transitions."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    BroadcastMessage,
    Delivery,
    IntercomManager,
)


ENTITY_ID = "assist_satellite.kitchen"


def _message(message_id: str, *, status: str = "queued_idle") -> BroadcastMessage:
    """Build a live queued Broadcast message for one satellite."""
    now = datetime.now(UTC)
    return BroadcastMessage(
        id=message_id,
        message=f"Message {message_id}",
        created_at=now.isoformat(),
        expires_at=now + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=[ENTITY_ID],
        deliveries={ENTITY_ID: Delivery(ENTITY_ID, status)},
    )


@pytest.mark.asyncio
async def test_disabling_during_idle_stability_wait_expires_without_announce(
    hass, monkeypatch
) -> None:
    """A disable racing the stability wait must stop a not-yet-started delivery."""
    manager = IntercomManager(hass)
    manager._enabled = True
    manager._store.async_save = AsyncMock()
    hass.states.get.return_value = SimpleNamespace(state="idle")
    announce = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", announce)

    item = _message("waiting")
    manager._history.appendleft(item)
    manager._queues[ENTITY_ID] = deque([item])

    async def disable_during_wait(_seconds: float) -> None:
        assert item.deliveries[ENTITY_ID].status == "waiting_idle"
        await manager.async_set_enabled(False)

    monkeypatch.setattr(intercom.asyncio, "sleep", disable_during_wait)

    await manager._async_drain(ENTITY_ID)

    announce.assert_not_awaited()
    assert item.deliveries[ENTITY_ID].status == "expired"
    assert item.deliveries[ENTITY_ID].detail == "broadcast_disabled"
    assert ENTITY_ID not in manager._queues


@pytest.mark.asyncio
async def test_disabling_during_announce_allows_in_flight_delivery_to_finish(
    hass, monkeypatch
) -> None:
    """Once announce starts, disabling Broadcast must not corrupt that delivery."""
    manager = IntercomManager(hass)
    manager._enabled = True
    manager._store.async_save = AsyncMock()
    hass.states.get.return_value = SimpleNamespace(state="idle")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(intercom.asyncio, "sleep", no_sleep)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_announce(*args, **kwargs) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(hass.services, "async_call", blocking_announce)

    item = _message("in-flight")
    manager._queues[ENTITY_ID] = deque([item])
    drain = asyncio.create_task(manager._async_drain(ENTITY_ID))
    await started.wait()

    assert item.deliveries[ENTITY_ID].status == "delivering"
    await manager.async_set_enabled(False)
    assert list(manager._queues[ENTITY_ID]) == [item]

    release.set()
    await drain

    assert item.deliveries[ENTITY_ID].status == "delivered"
    assert ENTITY_ID not in manager._queues


@pytest.mark.asyncio
async def test_failed_announce_does_not_block_next_queued_message(
    hass, monkeypatch
) -> None:
    """One satellite service failure must not strand later messages in its queue."""
    manager = IntercomManager(hass)
    manager._enabled = True
    hass.states.get.return_value = SimpleNamespace(state="idle")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(intercom.asyncio, "sleep", no_sleep)
    announce = AsyncMock(side_effect=[RuntimeError("speaker failed"), None])
    monkeypatch.setattr(hass.services, "async_call", announce)

    first = _message("first")
    second = _message("second")
    manager._queues[ENTITY_ID] = deque([first, second])

    await manager._async_drain(ENTITY_ID)

    assert announce.await_count == 2
    assert first.deliveries[ENTITY_ID].status == "failed"
    assert first.deliveries[ENTITY_ID].detail == "RuntimeError"
    assert second.deliveries[ENTITY_ID].status == "delivered"
    assert ENTITY_ID not in manager._queues


@pytest.mark.asyncio
async def test_satellite_becoming_busy_during_stability_wait_defers_delivery(
    hass, monkeypatch
) -> None:
    """Re-check satellite state after the stability delay before announcing."""
    manager = IntercomManager(hass)
    manager._enabled = True
    current_state = {"value": "idle"}
    hass.states.get.side_effect = lambda _entity_id: SimpleNamespace(
        state=current_state["value"]
    )
    announce = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", announce)

    item = _message("state-change")
    manager._queues[ENTITY_ID] = deque([item])

    async def become_busy(_seconds: float) -> None:
        current_state["value"] = "responding"

    monkeypatch.setattr(intercom.asyncio, "sleep", become_busy)
    await manager._async_drain(ENTITY_ID)

    announce.assert_not_awaited()
    assert item.deliveries[ENTITY_ID].status == "queued_busy"
    assert list(manager._queues[ENTITY_ID]) == [item]

    current_state["value"] = "idle"

    async def stay_idle(_seconds: float) -> None:
        return None

    monkeypatch.setattr(intercom.asyncio, "sleep", stay_idle)
    await manager._async_drain(ENTITY_ID)

    announce.assert_awaited_once()
    assert item.deliveries[ENTITY_ID].status == "delivered"
    assert ENTITY_ID not in manager._queues


def test_expiring_one_message_preserves_unrelated_queue_entries(hass) -> None:
    """Expiry must remove only the matching item from a satellite queue."""
    manager = IntercomManager(hass)
    expired = _message("expired", status="queued_busy")
    survivor = _message("survivor", status="queued_busy")
    manager._history.extendleft([survivor, expired])
    manager._queues[ENTITY_ID] = deque([expired, survivor])

    manager._expire(expired.id)

    assert expired.deliveries[ENTITY_ID].status == "expired"
    assert survivor.deliveries[ENTITY_ID].status == "queued_busy"
    assert list(manager._queues[ENTITY_ID]) == [survivor]
