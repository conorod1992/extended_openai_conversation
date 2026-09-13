"""Focused residual coverage for Broadcast/intercom state transitions."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    BroadcastMessage,
    Delivery,
    IntercomManager,
)


def _message(
    entity_ids: str | list[str],
    *,
    message_id: str = "message",
    status: str = "queued_idle",
    seconds: int = 30,
) -> BroadcastMessage:
    targets = [entity_ids] if isinstance(entity_ids, str) else entity_ids
    return BroadcastMessage(
        id=message_id,
        message="Hello",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=targets,
        deliveries={entity_id: Delivery(entity_id, status) for entity_id in targets},
    )


def test_refresh_state_listener_replaces_and_removes_subscription(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    tracked = ["assist_satellite.kitchen"]
    monkeypatch.setattr(manager, "_satellite_entity_ids", lambda: list(tracked))

    unsubscribe = Mock()
    tracker = Mock(return_value=unsubscribe)
    monkeypatch.setattr(intercom, "async_track_state_change_event", tracker)

    manager._refresh_state_listener()
    manager._refresh_state_listener()

    tracker.assert_called_once_with(
        hass,
        ["assist_satellite.kitchen"],
        manager._async_state_changed,
    )
    unsubscribe.assert_not_called()

    tracked.clear()
    manager._refresh_state_listener()

    unsubscribe.assert_called_once_with()
    assert manager._tracked_entities == set()
    assert manager._unsub_state is None


@pytest.mark.asyncio
async def test_drain_rechecks_enabled_after_idle_stability(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message(entity_id)
    manager._queues[entity_id] = deque([item])
    hass.states.get.return_value = SimpleNamespace(state="idle")

    async def disable_during_stability(_seconds: float) -> None:
        manager._enabled = False

    monkeypatch.setattr(intercom.asyncio, "sleep", disable_during_stability)
    hass.services.async_call = AsyncMock()

    await manager._async_drain(entity_id)

    assert item.deliveries[entity_id].status == "expired"
    assert item.deliveries[entity_id].detail == "broadcast_disabled"
    hass.services.async_call.assert_not_awaited()
    assert entity_id not in manager._queues


@pytest.mark.asyncio
async def test_drain_rechecks_expiry_after_idle_stability(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message(entity_id)
    manager._queues[entity_id] = deque([item])
    hass.states.get.return_value = SimpleNamespace(state="idle")

    async def expire_during_stability(_seconds: float) -> None:
        item.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    monkeypatch.setattr(intercom.asyncio, "sleep", expire_during_stability)
    hass.services.async_call = AsyncMock()

    await manager._async_drain(entity_id)

    assert item.deliveries[entity_id].status == "expired"
    hass.services.async_call.assert_not_awaited()
    assert entity_id not in manager._queues


@pytest.mark.asyncio
async def test_drain_rechecks_busy_state_after_idle_stability(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message(entity_id)
    manager._queues[entity_id] = deque([item])
    hass.states.get.return_value = SimpleNamespace(state="idle")

    async def become_busy_during_stability(_seconds: float) -> None:
        hass.states.get.return_value = SimpleNamespace(state="responding")

    monkeypatch.setattr(intercom.asyncio, "sleep", become_busy_during_stability)
    hass.services.async_call = AsyncMock()

    await manager._async_drain(entity_id)

    assert item.deliveries[entity_id].status == "queued_busy"
    hass.services.async_call.assert_not_awaited()
    assert list(manager._queues[entity_id]) == [item]


def test_expire_isolates_inflight_and_unrelated_queue_entries(hass) -> None:
    kitchen = "assist_satellite.kitchen"
    bedroom = "assist_satellite.bedroom"
    manager = IntercomManager(hass)

    target = _message([kitchen, bedroom])
    target.deliveries[bedroom].set("delivering")
    unrelated = _message(kitchen, message_id="other")
    manager._history.appendleft(target)
    manager._queues[kitchen] = deque([target, unrelated])
    manager._queues[bedroom] = deque([target])

    manager._expire(target.id)

    assert target.deliveries[kitchen].status == "expired"
    assert target.deliveries[bedroom].status == "delivering"
    assert list(manager._queues[kitchen]) == [unrelated]
    assert list(manager._queues[bedroom]) == [target]


def test_parser_returns_resolved_target_and_payload(monkeypatch, hass) -> None:
    manager = IntercomManager(hass)
    monkeypatch.setattr(
        manager,
        "catalog",
        lambda: {
            "satellites": [],
            "devices": [],
            "areas": [
                {"id": "kitchen", "name": "Kitchen", "aliases": ["Cooking Area"]}
            ],
            "floors": [],
            "labels": [],
        },
    )

    assert intercom.parse_targeted_broadcast(
        "Broadcast to cooking area: Dinner is ready", manager
    ) == ({"area_ids": ["kitchen"]}, "Dinner is ready")
    assert intercom.parse_targeted_broadcast(
        "Tell Kitchen that lights out", manager
    ) == ({"area_ids": ["kitchen"]}, "lights out")
