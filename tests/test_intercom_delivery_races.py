"""Race and catalog coverage for Broadcast intercom behavior."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
    BroadcastMessage,
    Delivery,
    IntercomManager,
)


ENTITY_ID = "assist_satellite.kitchen"


def _message(*, status: str = "queued_idle") -> BroadcastMessage:
    return BroadcastMessage(
        id="race-message",
        message="Dinner is ready",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=[ENTITY_ID],
        deliveries={ENTITY_ID: Delivery(ENTITY_ID, status)},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("second_state", [None, SimpleNamespace(state="responding")])
async def test_drain_rechecks_satellite_after_idle_stability_window(
    hass, monkeypatch, second_state
) -> None:
    """A satellite that disappears or becomes busy during the wait is not announced to."""
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message()
    manager._queues[ENTITY_ID] = deque([item])
    hass.states.get.side_effect = [
        SimpleNamespace(state="idle"),
        second_state,
        second_state,
    ]
    monkeypatch.setattr(intercom.asyncio, "sleep", AsyncMock())
    service_call = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", service_call)
    reschedule = Mock()
    monkeypatch.setattr(manager, "_schedule_drain", reschedule)

    await manager._async_drain(ENTITY_ID)

    assert item.deliveries[ENTITY_ID].status == "queued_busy"
    assert list(manager._queues[ENTITY_ID]) == [item]
    service_call.assert_not_awaited()
    reschedule.assert_not_called()


@pytest.mark.asyncio
async def test_drain_reschedules_if_busy_satellite_turns_idle_during_cleanup(
    hass, monkeypatch
) -> None:
    """A busy-to-idle race at drain exit cannot strand queued work."""
    manager = IntercomManager(hass)
    manager._enabled = True
    manager._draining.add(ENTITY_ID)
    item = _message(status="queued_busy")
    manager._queues[ENTITY_ID] = deque([item])
    hass.states.get.side_effect = [
        SimpleNamespace(state="responding"),
        SimpleNamespace(state="idle"),
    ]
    reschedule = Mock()
    monkeypatch.setattr(manager, "_schedule_drain", reschedule)

    await manager._async_drain(ENTITY_ID)

    assert item.deliveries[ENTITY_ID].status == "queued_busy"
    assert ENTITY_ID not in manager._draining
    reschedule.assert_called_once_with(ENTITY_ID)


def test_catalog_projects_reachable_registry_metadata(hass, monkeypatch) -> None:
    """The UI catalog exposes only metadata reachable from announce-capable satellites."""
    capable = SimpleNamespace(
        entity_id=ENTITY_ID,
        state="idle",
        attributes={
            intercom.ATTR_SUPPORTED_FEATURES: intercom.ANNOUNCE_FEATURE,
            intercom.ATTR_FRIENDLY_NAME: "Kitchen Voice",
        },
    )
    incapable = SimpleNamespace(
        entity_id="assist_satellite.legacy",
        state="idle",
        attributes={intercom.ATTR_SUPPORTED_FEATURES: 0},
    )
    hass.states.async_all.return_value = [capable, incapable]

    entity = SimpleNamespace(
        area_id=None,
        device_id="device-1",
        labels={"label-entity"},
        aliases={" Voice Alias ", ""},
    )
    device = SimpleNamespace(
        id="device-1",
        area_id="area-1",
        labels={"label-device"},
        aliases={"Kitchen Speaker"},
        name_by_user=None,
        name="Kitchen Device",
    )
    area = SimpleNamespace(
        id="area-1",
        name="Kitchen",
        floor_id="floor-1",
        labels={"label-area"},
        aliases={"Cooking Area"},
    )
    floor = SimpleNamespace(
        floor_id="floor-1", name="Ground", aliases={"Downstairs"}
    )
    label_rows = [
        SimpleNamespace(label_id="label-entity", name="Voice", aliases=set()),
        SimpleNamespace(label_id="label-device", name="Speaker", aliases=set()),
        SimpleNamespace(label_id="label-area", name="Common", aliases=set()),
        SimpleNamespace(label_id="irrelevant", name="Ignore", aliases=set()),
    ]

    entity_registry = SimpleNamespace(
        async_get=lambda entity_id: entity if entity_id == ENTITY_ID else None
    )
    device_registry = SimpleNamespace(
        async_get=lambda device_id: device if device_id == "device-1" else None
    )
    area_registry = SimpleNamespace(
        async_get_area=lambda area_id: area if area_id == "area-1" else None,
        async_list_areas=lambda: [area],
    )
    floor_registry = SimpleNamespace(async_list_floors=lambda: [floor])
    label_registry = SimpleNamespace(async_list_labels=lambda: label_rows)
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entity_registry)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: device_registry)
    monkeypatch.setattr(intercom.ar, "async_get", lambda _hass: area_registry)
    monkeypatch.setattr(intercom.fr, "async_get", lambda _hass: floor_registry)
    monkeypatch.setattr(intercom.lr, "async_get", lambda _hass: label_registry)

    manager = IntercomManager(hass)
    catalog = manager.catalog()

    assert catalog["satellites"] == [
        {
            "id": ENTITY_ID,
            "name": "Kitchen Voice",
            "aliases": ["Voice Alias"],
            "state": "idle",
            "area_id": "area-1",
            "device_id": "device-1",
        }
    ]
    assert catalog["areas"] == [
        {"id": "area-1", "name": "Kitchen", "aliases": ["Cooking Area"]}
    ]
    assert catalog["floors"] == [
        {"id": "floor-1", "name": "Ground", "aliases": ["Downstairs"]}
    ]
    assert {item["id"] for item in catalog["labels"]} == {
        "label-entity",
        "label-device",
        "label-area",
    }
    assert catalog["devices"] == [
        {
            "id": "device-1",
            "name": "Kitchen Device",
            "aliases": ["Kitchen Speaker"],
        }
    ]


# Consolidated queue, expiry, listener, and delivery-race regressions.

def _queue_message(message_id: str, *, status: str = "queued_idle") -> BroadcastMessage:
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

    item = _queue_message("waiting")
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

    item = _queue_message("in-flight")
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

    first = _queue_message("first")
    second = _queue_message("second")
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

    item = _queue_message("state-change")
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
    expired = _queue_message("expired", status="queued_busy")
    survivor = _queue_message("survivor", status="queued_busy")
    manager._history.extendleft([survivor, expired])
    manager._queues[ENTITY_ID] = deque([expired, survivor])

    manager._expire(expired.id)

    assert expired.deliveries[ENTITY_ID].status == "expired"
    assert survivor.deliveries[ENTITY_ID].status == "queued_busy"
    assert list(manager._queues[ENTITY_ID]) == [survivor]


def _residual_message(
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
async def test_drain_rechecks_expiry_after_idle_stability(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _residual_message(entity_id)
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


def test_expire_isolates_inflight_and_unrelated_queue_entries(hass) -> None:
    kitchen = "assist_satellite.kitchen"
    bedroom = "assist_satellite.bedroom"
    manager = IntercomManager(hass)

    target = _residual_message([kitchen, bedroom])
    target.deliveries[bedroom].set("delivering")
    unrelated = _residual_message(kitchen, message_id="other")
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


def _residual2_message(
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
    item = _residual2_message(entity_id, message_id="message", status="queued_idle")
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
    first = _residual2_message(entity_id, message_id="first", status="queued_idle", message="First")
    replacement = _residual2_message(
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
