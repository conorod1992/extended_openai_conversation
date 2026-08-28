"""Tests for targeted intercom broadcasts."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    BroadcastMessage,
    Delivery,
    IntercomManager,
    parse_targeted_broadcast,
)


def test_targeted_parser_resolves_named_destination(monkeypatch) -> None:
    manager = SimpleNamespace()
    manager.catalog = lambda: {
        "satellites": [],
        "devices": [],
        "areas": [{"id": "kitchen", "name": "Kitchen"}],
        "floors": [],
        "labels": [],
    }
    manager.resolve_named_target = lambda name: (
        {"area_ids": ["kitchen"], "name": "Kitchen"}
        if name.casefold() == "kitchen"
        else None
    )

    assert parse_targeted_broadcast(
        "Broadcast to kitchen that dinner is ready", manager
    ) == ({"area_ids": ["kitchen"]}, "dinner is ready")
    assert parse_targeted_broadcast("What time is dinner?", manager) is None


def test_targeted_parser_supports_whole_home(monkeypatch) -> None:
    manager = SimpleNamespace()
    manager.catalog = lambda: {
        "satellites": [], "devices": [], "areas": [], "floors": [], "labels": []
    }
    manager.resolve_named_target = lambda name: (
        {"whole_home": True, "name": name}
        if name.casefold() == "everyone"
        else None
    )

    assert parse_targeted_broadcast("Tell everyone dinner is ready", manager) == (
        {"whole_home": True},
        "dinner is ready",
    )


@pytest.mark.asyncio
async def test_busy_satellite_is_queued_without_announce(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    monkeypatch.setattr(manager, "resolve_targets", lambda **kwargs: ["assist_satellite.kitchen"])
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="responding")
    call = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", call)

    result = await manager.async_send("Dinner is ready", entity_ids=["assist_satellite.kitchen"])
    await manager._async_drain("assist_satellite.kitchen")

    assert result["deliveries"]["assist_satellite.kitchen"]["status"] == "queued_busy"
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_idle_satellite_delivers_after_stability_check(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    monkeypatch.setattr(manager, "resolve_targets", lambda **kwargs: ["assist_satellite.kitchen"])
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    call = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", call)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.intercom.asyncio.sleep",
        no_sleep,
    )
    result = await manager.async_send("Dinner is ready", entity_ids=["assist_satellite.kitchen"])
    await manager._async_drain("assist_satellite.kitchen")

    call.assert_awaited_once()
    assert manager.history()[0]["deliveries"]["assist_satellite.kitchen"]["status"] == "delivered"
    assert result["id"] == manager.history()[0]["id"]


def test_expire_removes_pending_delivery(hass) -> None:
    manager = IntercomManager(hass)
    from datetime import UTC, datetime, timedelta

    item = BroadcastMessage(
        id="message-1",
        message="Test",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=["assist_satellite.kitchen"],
        deliveries={"assist_satellite.kitchen": Delivery("assist_satellite.kitchen", "queued_busy")},
    )
    manager._history.appendleft(item)
    manager._queues["assist_satellite.kitchen"] = deque([item])

    manager._expire("message-1")

    assert item.deliveries["assist_satellite.kitchen"].status == "expired"
    assert "assist_satellite.kitchen" not in manager._queues


def test_expire_cleans_queued_delivery_missing_from_history(hass) -> None:
    manager = IntercomManager(hass)
    from datetime import UTC, datetime, timedelta

    item = BroadcastMessage(
        id="evicted-message",
        message="Test",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=["assist_satellite.kitchen"],
        deliveries={"assist_satellite.kitchen": Delivery("assist_satellite.kitchen", "queued_busy")},
    )
    manager._queues["assist_satellite.kitchen"] = deque([item])

    manager._expire("evicted-message")

    assert item.deliveries["assist_satellite.kitchen"].status == "expired"
    assert "assist_satellite.kitchen" not in manager._queues


def test_state_listener_refreshes_when_satellites_change(hass, monkeypatch) -> None:
    tracked: list[list[str]] = []
    unsubscribed: list[bool] = []

    def fake_track(_hass, entity_ids, _callback):
        tracked.append(list(entity_ids))

        def unsubscribe() -> None:
            unsubscribed.append(True)

        return unsubscribe

    monkeypatch.setattr(intercom, "async_track_state_change_event", fake_track)
    hass.states.async_all.return_value = [
        SimpleNamespace(entity_id="assist_satellite.kitchen")
    ]
    manager = IntercomManager(hass)

    hass.states.async_all.return_value = [
        SimpleNamespace(entity_id="assist_satellite.kitchen"),
        SimpleNamespace(entity_id="assist_satellite.bedroom"),
    ]
    manager._refresh_state_listener()

    assert tracked == [
        ["assist_satellite.kitchen"],
        ["assist_satellite.bedroom", "assist_satellite.kitchen"],
    ]
    assert unsubscribed == [True]
