"""Focused residual coverage for Intercom lifecycle and queue races."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    BroadcastMessage,
    Delivery,
    IntercomManager,
)


def _message(
    entity_id: str,
    *,
    message_id: str,
    status: str = "queued_busy",
    message: str = "Hello",
) -> BroadcastMessage:
    return BroadcastMessage(
        id=message_id,
        message=message,
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=[entity_id],
        deliveries={entity_id: Delivery(entity_id, status)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "expected_enabled"),
    [(None, False), ({}, False), ({"enabled": True}, True)],
)
async def test_async_initialize_loads_enabled_setting_once(
    hass, stored: dict[str, Any] | None, expected_enabled: bool
) -> None:
    manager = IntercomManager(hass)
    load = AsyncMock(return_value=stored)
    manager._store.async_load = load

    await manager.async_initialize()
    await manager.async_initialize()

    assert manager.enabled is expected_enabled
    assert manager._loaded is True
    load.assert_awaited_once_with()


def test_resolve_targets_skips_nonmatching_capable_satellite(hass, monkeypatch) -> None:
    matching = "assist_satellite.kitchen"
    other = "assist_satellite.bedroom"
    manager = IntercomManager(hass)
    hass.states.async_all.return_value = [
        SimpleNamespace(
            entity_id=matching,
            state="idle",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        ),
        SimpleNamespace(
            entity_id=other,
            state="idle",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        ),
    ]
    registry = SimpleNamespace(
        async_get=lambda entity_id: SimpleNamespace(device_id=f"device-{entity_id}")
    )
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(manager, "_refresh_state_listener", lambda: None)
    monkeypatch.setattr(
        manager,
        "_target_matches",
        lambda entity_id, **_kwargs: entity_id == matching,
    )

    assert manager.resolve_targets(label_ids=["downstairs"]) == [matching]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ttl_seconds", "expected_ttl"), [(1, 5), (7200, 3600)])
async def test_async_send_expiry_callback_expires_message(
    hass, monkeypatch, ttl_seconds: int, expected_ttl: int
) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(manager, "resolve_targets", lambda **_kwargs: [entity_id])
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="responding")
    scheduled: list[tuple[int, Any]] = []
    monkeypatch.setattr(
        intercom,
        "async_call_later",
        lambda _hass, delay, callback: scheduled.append((delay, callback)),
    )

    result = await manager.async_send("Hello", ttl_seconds=ttl_seconds)

    assert scheduled[0][0] == expected_ttl
    scheduled[0][1](datetime.now(UTC))

    assert manager.history()[0]["id"] == result["id"]
    assert manager.history()[0]["deliveries"][entity_id]["status"] == "expired"
    assert entity_id not in manager._queues


def test_schedule_drain_creates_only_one_task_per_entity(hass) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    created: list[Any] = []

    def create_task(coro: Any) -> None:
        created.append(coro)

    hass.async_create_task = create_task

    manager._schedule_drain(entity_id)
    manager._schedule_drain(entity_id)

    assert entity_id in manager._draining
    assert len(created) == 1
    created[0].close()


@pytest.mark.asyncio
async def test_delivery_marked_expired_during_stability_wait_is_removed(
    hass, monkeypatch
) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message(entity_id, message_id="message", status="queued_idle")
    manager._queues[entity_id] = deque([item])
    hass.states.get.return_value = SimpleNamespace(state="idle")
    announce = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", announce)

    async def mark_expired(_seconds: float) -> None:
        item.deliveries[entity_id].set("expired")

    monkeypatch.setattr(intercom.asyncio, "sleep", mark_expired)

    await manager._async_drain(entity_id)

    announce.assert_not_awaited()
    assert item.deliveries[entity_id].status == "expired"
    assert entity_id not in manager._queues


@pytest.mark.asyncio
async def test_drain_with_missing_queue_cleans_draining_flag(hass) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._draining.add(entity_id)

    await manager._async_drain(entity_id)

    assert entity_id not in manager._draining
    assert entity_id not in manager._queues


@pytest.mark.asyncio
async def test_queue_head_changed_during_announce_is_not_popped(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    first = _message(entity_id, message_id="first", status="queued_idle", message="First")
    replacement = _message(
        entity_id,
        message_id="replacement",
        status="queued_busy",
        message="Replacement",
    )
    queue = deque([first])
    manager._queues[entity_id] = queue
    hass.states.get.side_effect = [
        SimpleNamespace(state="idle"),
        SimpleNamespace(state="idle"),
        SimpleNamespace(state="responding"),
        SimpleNamespace(state="responding"),
    ]

    async def no_sleep(_seconds: float) -> None:
        return None

    async def announce(*_args: Any, **_kwargs: Any) -> None:
        assert queue[0] is first
        queue.popleft()
        queue.appendleft(replacement)

    monkeypatch.setattr(intercom.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(hass.services, "async_call", announce)

    await manager._async_drain(entity_id)

    assert first.deliveries[entity_id].status == "delivered"
    assert list(manager._queues[entity_id]) == [replacement]
    assert replacement.deliveries[entity_id].status == "queued_busy"
