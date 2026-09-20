"""Tests for targeted Broadcast announcements."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses.intercom import (
    ANNOUNCE_FEATURE,
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
        "areas": [{"id": "kitchen", "name": "Kitchen", "aliases": []}],
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


def test_targeted_parser_checks_aliases(monkeypatch) -> None:
    manager = SimpleNamespace()
    manager.catalog = lambda: {
        "satellites": [],
        "devices": [],
        "areas": [
            {"id": "kitchen", "name": "Kitchen", "aliases": ["Cooking area"]}
        ],
        "floors": [],
        "labels": [],
    }
    manager.resolve_named_target = lambda name: (
        {"area_ids": ["kitchen"], "name": "Kitchen"}
        if name.casefold() == "cooking area"
        else None
    )

    assert parse_targeted_broadcast(
        "Tell cooking area dinner is ready", manager
    ) == ({"area_ids": ["kitchen"]}, "dinner is ready")


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
async def test_disabled_broadcast_rejects_send(hass) -> None:
    manager = IntercomManager(hass)

    with pytest.raises(HomeAssistantError, match="Broadcast is disabled"):
        await manager.async_send(
            "Dinner is ready", entity_ids=["assist_satellite.kitchen"]
        )


@pytest.mark.asyncio
async def test_busy_satellite_is_queued_without_announce(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    manager._enabled = True
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
    manager._enabled = True
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


@pytest.mark.asyncio
async def test_expiry_during_idle_stability_wait_prevents_delivery(
    hass, monkeypatch
) -> None:
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(manager, "resolve_targets", lambda **kwargs: ["assist_satellite.kitchen"])
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = SimpleNamespace(state="idle")
    call = AsyncMock()
    monkeypatch.setattr(hass.services, "async_call", call)

    result = await manager.async_send(
        "Dinner is ready", entity_ids=["assist_satellite.kitchen"]
    )

    async def expire_during_sleep(_seconds):
        manager._expire(result["id"])

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.intercom.asyncio.sleep",
        expire_during_sleep,
    )

    await manager._async_drain("assist_satellite.kitchen")

    call.assert_not_awaited()
    assert manager.history()[0]["deliveries"]["assist_satellite.kitchen"]["status"] == "expired"
    assert "assist_satellite.kitchen" not in manager._queues


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


def test_expire_preserves_in_flight_delivery(hass) -> None:
    manager = IntercomManager(hass)
    from datetime import UTC, datetime, timedelta

    item = BroadcastMessage(
        id="in-flight-message",
        message="Test",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=["assist_satellite.kitchen"],
        deliveries={
            "assist_satellite.kitchen": Delivery(
                "assist_satellite.kitchen", "delivering"
            )
        },
    )
    manager._history.appendleft(item)
    manager._queues["assist_satellite.kitchen"] = deque([item])

    manager._expire("in-flight-message")

    assert item.deliveries["assist_satellite.kitchen"].status == "delivering"
    assert list(manager._queues["assist_satellite.kitchen"]) == [item]


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


# Consolidated routing, metadata, persistence, and parser contracts.

class _CoverageEntityRegistry:
    def __init__(self, entries):
        self.entries = entries

    def async_get(self, entity_id):
        return self.entries.get(entity_id)


class _CoverageLookupRegistry:
    def __init__(self, entries):
        self.entries = entries

    def async_get(self, item_id):
        return self.entries.get(item_id)

    def async_get_area(self, item_id):
        return self.entries.get(item_id)


def _coverage_message(entity_id: str, *, status: str = "queued_busy", seconds: int = 30):
    return BroadcastMessage(
        id="message",
        message="Hello",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=[entity_id],
        deliveries={entity_id: Delivery(entity_id, status)},
    )


def test_aliases_are_filtered_trimmed_sorted_and_unique() -> None:
    value = SimpleNamespace(aliases=[" kitchen ", "", None, "Bedroom", "kitchen"])
    assert intercom._aliases(value) == ["Bedroom", "kitchen"]


@pytest.mark.asyncio
async def test_send_rejects_empty_message_and_no_targets(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    manager._enabled = True

    with pytest.raises(HomeAssistantError, match="cannot be empty"):
        await manager.async_send("   ", whole_home=True)

    monkeypatch.setattr(manager, "resolve_targets", lambda **_kwargs: [])
    with pytest.raises(HomeAssistantError, match="No matching"):
        await manager.async_send("Hello", whole_home=True)


def test_entity_area_falls_back_to_device_area(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    entities = _CoverageEntityRegistry(
        {
            "assist_satellite.kitchen": SimpleNamespace(
                area_id=None, device_id="device-1", labels=set()
            )
        }
    )
    devices = _CoverageLookupRegistry({"device-1": SimpleNamespace(area_id="kitchen", labels=set())})
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)

    assert manager._entity_area_id("assist_satellite.kitchen") == "kitchen"
    assert manager._entity_area_id("assist_satellite.missing") is None


def test_target_matching_supports_direct_device_area_floor_and_labels(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    entity = SimpleNamespace(
        device_id="device-1", area_id="kitchen", labels={"entity-label"}
    )
    entities = _CoverageEntityRegistry({"assist_satellite.kitchen": entity})
    devices = _CoverageLookupRegistry(
        {"device-1": SimpleNamespace(area_id="kitchen", labels={"device-label"})}
    )
    areas = _CoverageLookupRegistry(
        {"kitchen": SimpleNamespace(floor_id="ground", labels={"area-label"})}
    )
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)
    monkeypatch.setattr(intercom.ar, "async_get", lambda _hass: areas)

    def matches(**targets):
        defaults = dict(
            entity_ids=set(), device_ids=set(), area_ids=set(), floor_ids=set(), label_ids=set()
        )
        defaults.update(targets)
        return manager._target_matches("assist_satellite.kitchen", **defaults)

    assert matches(entity_ids={"assist_satellite.kitchen"})
    assert matches(device_ids={"device-1"})
    assert matches(area_ids={"kitchen"})
    assert matches(floor_ids={"ground"})
    assert matches(label_ids={"entity-label"})
    assert matches(label_ids={"area-label"})
    assert matches(label_ids={"device-label"})
    assert not matches(label_ids={"other"})


def test_resolve_targets_excludes_origin_and_non_announce_capable(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    states = [
        SimpleNamespace(
            entity_id="assist_satellite.origin",
            state="idle",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        ),
        SimpleNamespace(
            entity_id="assist_satellite.same_device",
            state="idle",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        ),
        SimpleNamespace(
            entity_id="assist_satellite.good",
            state="idle",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        ),
        SimpleNamespace(
            entity_id="assist_satellite.unsupported",
            state="idle",
            attributes={"supported_features": 0},
        ),
    ]
    hass.states.async_all.return_value = states
    entries = _CoverageEntityRegistry(
        {
            "assist_satellite.origin": SimpleNamespace(device_id="origin-device"),
            "assist_satellite.same_device": SimpleNamespace(device_id="origin-device"),
            "assist_satellite.good": SimpleNamespace(device_id="other-device"),
            "assist_satellite.unsupported": SimpleNamespace(device_id="other-device"),
        }
    )
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entries)
    monkeypatch.setattr(manager, "_refresh_state_listener", lambda: None)

    assert manager.resolve_targets(
        whole_home=True,
        origin_entity_id="assist_satellite.origin",
        origin_device_id="origin-device",
    ) == ["assist_satellite.good"]


@pytest.mark.asyncio
async def test_drain_marks_expired_when_disabled_or_ttl_elapsed(hass) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = False
    disabled = _coverage_message(entity_id)
    manager._queues[entity_id] = deque([disabled])

    await manager._async_drain(entity_id)

    assert disabled.deliveries[entity_id].status == "expired"
    assert disabled.deliveries[entity_id].detail == "broadcast_disabled"

    manager._enabled = True
    expired = _coverage_message(entity_id, seconds=-1)
    manager._queues[entity_id] = deque([expired])

    await manager._async_drain(entity_id)

    assert expired.deliveries[entity_id].status == "expired"


def test_state_change_only_schedules_idle_queued_entity(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    manager._queues["assist_satellite.kitchen"] = deque()
    scheduled: list[str] = []
    monkeypatch.setattr(manager, "_schedule_drain", scheduled.append)

    manager._async_state_changed(
        SimpleNamespace(
            data={
                "entity_id": "assist_satellite.kitchen",
                "new_state": SimpleNamespace(state="idle"),
            }
        )
    )
    manager._async_state_changed(
        SimpleNamespace(
            data={
                "entity_id": "assist_satellite.kitchen",
                "new_state": SimpleNamespace(state="responding"),
            }
        )
    )
    manager._async_state_changed(
        SimpleNamespace(
            data={"entity_id": "assist_satellite.other", "new_state": None}
        )
    )

    assert scheduled == ["assist_satellite.kitchen"]


@pytest.mark.asyncio
async def test_async_get_intercom_reuses_manager_and_initializes_each_access(hass, monkeypatch) -> None:
    initialize = AsyncMock()
    monkeypatch.setattr(IntercomManager, "async_initialize", initialize)

    first = await intercom.async_get_intercom(hass)
    second = await intercom.async_get_intercom(hass)

    assert first is second
    assert initialize.await_count == 2


def test_targeted_request_detection_is_deliberately_narrow() -> None:
    assert intercom.is_targeted_broadcast_request("Broadcast to kitchen dinner is ready")
    assert intercom.is_targeted_broadcast_request("Tell upstairs good night")
    assert not intercom.is_targeted_broadcast_request("Broadcast dinner is ready")
    assert not intercom.is_targeted_broadcast_request("What should I tell everyone?")


def test_resolve_named_target_handles_whole_home_alias_and_unknown(monkeypatch, hass) -> None:
    manager = IntercomManager(hass)
    monkeypatch.setattr(
        manager,
        "catalog",
        lambda: {
            "satellites": [],
            "devices": [],
            "areas": [{"id": "kitchen", "name": "Kitchen", "aliases": ["Cooking Area"]}],
            "floors": [],
            "labels": [],
        },
    )

    assert manager.resolve_named_target(" the whole house ") == {
        "whole_home": True,
        "name": "the whole house",
    }
    assert manager.resolve_named_target("the cooking area") == {
        "area_ids": ["kitchen"],
        "name": "Kitchen",
    }
    assert manager.resolve_named_target("garage") is None


def test_parser_rejects_known_target_without_payload(monkeypatch, hass) -> None:
    manager = IntercomManager(hass)
    monkeypatch.setattr(
        manager,
        "catalog",
        lambda: {
            "satellites": [],
            "devices": [],
            "areas": [{"id": "kitchen", "name": "Kitchen", "aliases": []}],
            "floors": [],
            "labels": [],
        },
    )
    monkeypatch.setattr(
        manager,
        "resolve_named_target",
        lambda name: {"area_ids": ["kitchen"], "name": "Kitchen"}
        if name.casefold() == "kitchen"
        else None,
    )

    assert intercom.parse_targeted_broadcast("Broadcast to kitchen", manager) is None


def test_target_match_without_device_uses_entity_labels(hass, monkeypatch) -> None:
    """A satellite with no device registry link can still match its own labels."""
    manager = IntercomManager(hass)
    entity = SimpleNamespace(
        entity_id="assist_satellite.porch",
        device_id=None,
        area_id=None,
        labels={"outside"},
    )
    registry = SimpleNamespace(async_get=lambda _entity_id: entity)
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: registry)

    assert manager._target_matches(
        entity.entity_id,
        entity_ids=set(),
        device_ids=set(),
        area_ids=set(),
        floor_ids=set(),
        label_ids={"outside"},
    )


def _remaining_message(
    entity_id: str,
    *,
    message_id: str,
    status: str = "queued_busy",
    include_delivery: bool = True,
) -> BroadcastMessage:
    deliveries = (
        {entity_id: Delivery(entity_id, status)} if include_delivery else {}
    )
    return BroadcastMessage(
        id=message_id,
        message="Hello",
        created_at=datetime.now(UTC).isoformat(),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=[entity_id],
        deliveries=deliveries,
    )


class _RemainingEntityRegistry:
    def __init__(self, entries: dict[str, object]) -> None:
        self.entries = entries

    def async_get(self, entity_id: str):
        return self.entries.get(entity_id)


class _RemainingDeviceRegistry:
    def __init__(self, entries: dict[str, object]) -> None:
        self.entries = entries

    def async_get(self, device_id: str):
        return self.entries.get(device_id)


class _RemainingAreaRegistry:
    def __init__(self, entries: dict[str, object]) -> None:
        self.entries = entries

    def async_get_area(self, area_id: str | None):
        return self.entries.get(area_id)

    def async_list_areas(self):
        return list(self.entries.values())


class _RemainingListRegistry:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def async_list_floors(self):
        return list(self.items)

    def async_list_labels(self):
        return list(self.items)


@pytest.mark.asyncio
async def test_disabling_handles_missing_terminal_pending_and_inflight_deliveries(hass) -> None:
    """Turning Broadcast off should clean queues without corrupting terminal/in-flight state."""
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    manager._store.async_save = AsyncMock()

    missing = _remaining_message(entity_id, message_id="missing", include_delivery=False)
    delivered = _remaining_message(entity_id, message_id="delivered", status="delivered")
    failed = _remaining_message(entity_id, message_id="failed", status="failed")
    expired = _remaining_message(entity_id, message_id="expired", status="expired")
    pending = _remaining_message(entity_id, message_id="pending", status="waiting_idle")
    inflight = _remaining_message(entity_id, message_id="inflight", status="delivering")
    manager._queues[entity_id] = deque(
        [missing, delivered, failed, expired, pending, inflight]
    )

    await manager.async_set_enabled(False)

    assert pending.deliveries[entity_id].status == "expired"
    assert pending.deliveries[entity_id].detail == "broadcast_disabled"
    assert delivered.deliveries[entity_id].status == "delivered"
    assert failed.deliveries[entity_id].status == "failed"
    assert expired.deliveries[entity_id].status == "expired"
    assert list(manager._queues[entity_id]) == [inflight]
    manager._store.async_save.assert_awaited_once_with({"enabled": False})


def test_capability_and_area_resolution_cover_absent_registry_metadata(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)

    assert manager._state_announce_capable(None) is False
    assert manager._state_announce_capable(SimpleNamespace(attributes={})) is False
    assert manager._state_announce_capable(
        SimpleNamespace(attributes={"supported_features": ANNOUNCE_FEATURE})
    ) is True

    entities = _RemainingEntityRegistry(
        {
            "assist_satellite.direct": SimpleNamespace(
                area_id="kitchen", device_id="device-direct", labels=set()
            ),
            "assist_satellite.device_missing": SimpleNamespace(
                area_id=None, device_id="missing-device", labels=set()
            ),
            "assist_satellite.no_device": SimpleNamespace(
                area_id=None, device_id=None, labels=set()
            ),
        }
    )
    devices = _RemainingDeviceRegistry({})
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)

    assert manager._entity_area_id("assist_satellite.direct") == "kitchen"
    assert manager._entity_area_id("assist_satellite.device_missing") is None
    assert manager._entity_area_id("assist_satellite.no_device") is None


def test_target_matching_handles_missing_entity_and_missing_device(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    entities = _RemainingEntityRegistry(
        {
            "assist_satellite.kitchen": SimpleNamespace(
                area_id=None,
                device_id="missing-device",
                labels=set(),
            )
        }
    )
    devices = _RemainingDeviceRegistry({})
    areas = _RemainingAreaRegistry({})
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)
    monkeypatch.setattr(intercom.ar, "async_get", lambda _hass: areas)

    empty = dict(
        entity_ids=set(),
        device_ids=set(),
        area_ids=set(),
        floor_ids=set(),
        label_ids={"not-present"},
    )
    assert not manager._target_matches("assist_satellite.missing", **empty)
    assert not manager._target_matches("assist_satellite.kitchen", **empty)


def test_resolve_targets_keeps_registryless_satellite_when_whole_home(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.portable"
    manager = IntercomManager(hass)
    hass.states.async_all.return_value = [
        SimpleNamespace(
            entity_id=entity_id,
            state="idle",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        )
    ]
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: _RemainingEntityRegistry({}))
    monkeypatch.setattr(manager, "_refresh_state_listener", lambda: None)

    assert manager.resolve_targets(
        whole_home=True,
        origin_device_id="origin-device",
    ) == [entity_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(("ttl_seconds", "expected_ttl"), [(-10, 5), (9999, 3600)])
async def test_send_clamps_ttl_and_handles_missing_state(
    hass, monkeypatch, ttl_seconds: int, expected_ttl: int
) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    monkeypatch.setattr(manager, "resolve_targets", lambda **_kwargs: [entity_id])
    monkeypatch.setattr(manager, "_schedule_drain", lambda _entity_id: None)
    hass.states.get.return_value = None
    scheduled: list[tuple[int, object]] = []
    monkeypatch.setattr(
        intercom,
        "async_call_later",
        lambda _hass, delay, callback: scheduled.append((delay, callback)),
    )

    result = await manager.async_send(" Hello ", ttl_seconds=ttl_seconds)

    assert result["message"] == "Hello"
    assert result["deliveries"][entity_id]["status"] == "queued_busy"
    assert scheduled[0][0] == expected_ttl


def test_expire_cleans_same_message_entries_without_delivery_or_already_terminal(hass) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    missing = _remaining_message(entity_id, message_id="target", include_delivery=False)
    delivered = _remaining_message(entity_id, message_id="target", status="delivered")
    unrelated = _remaining_message(entity_id, message_id="other", status="queued_busy")
    manager._queues[entity_id] = deque([missing, delivered, unrelated])

    manager._expire("target")

    assert list(manager._queues[entity_id]) == [unrelated]
    assert delivered.deliveries[entity_id].status == "delivered"


def test_catalog_handles_registryless_satellites_and_filters_related_metadata(
    hass, monkeypatch
) -> None:
    kitchen_entity = "assist_satellite.kitchen"
    portable_entity = "assist_satellite.portable"
    hass.states.async_all.return_value = [
        SimpleNamespace(
            entity_id=kitchen_entity,
            state="idle",
            attributes={
                "supported_features": ANNOUNCE_FEATURE,
                "friendly_name": "Kitchen Satellite",
            },
        ),
        SimpleNamespace(
            entity_id=portable_entity,
            state="responding",
            attributes={"supported_features": ANNOUNCE_FEATURE},
        ),
        SimpleNamespace(
            entity_id="assist_satellite.unsupported",
            state="idle",
            attributes={"supported_features": 0},
        ),
    ]
    entities = _RemainingEntityRegistry(
        {
            kitchen_entity: SimpleNamespace(
                area_id="kitchen",
                device_id="device-1",
                labels={"entity-label"},
                aliases=["Cooker"],
            )
        }
    )
    devices = _RemainingDeviceRegistry(
        {
            "device-1": SimpleNamespace(
                id="device-1",
                area_id="kitchen",
                labels={"device-label"},
                aliases=["Kitchen Hub"],
                name_by_user=None,
                name="Kitchen Device",
            )
        }
    )
    areas = _RemainingAreaRegistry(
        {
            "kitchen": SimpleNamespace(
                id="kitchen",
                name="Kitchen",
                aliases=["Cooking Area"],
                floor_id="ground",
                labels={"area-label"},
            ),
            "garage": SimpleNamespace(
                id="garage",
                name="Garage",
                aliases=[],
                floor_id="ground",
                labels=set(),
            ),
        }
    )
    floors = _RemainingListRegistry(
        [
            SimpleNamespace(
                floor_id="ground", name="Ground Floor", aliases=["Downstairs"]
            ),
            SimpleNamespace(floor_id="upper", name="Upper Floor", aliases=[]),
        ]
    )
    labels = _RemainingListRegistry(
        [
            SimpleNamespace(label_id="entity-label", name="Entity Label", aliases=[]),
            SimpleNamespace(label_id="area-label", name="Area Label", aliases=[]),
            SimpleNamespace(label_id="device-label", name="Device Label", aliases=[]),
            SimpleNamespace(label_id="unused-label", name="Unused Label", aliases=[]),
        ]
    )
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)
    monkeypatch.setattr(intercom.ar, "async_get", lambda _hass: areas)
    monkeypatch.setattr(intercom.fr, "async_get", lambda _hass: floors)
    monkeypatch.setattr(intercom.lr, "async_get", lambda _hass: labels)

    result = IntercomManager(hass).catalog()

    assert [item["id"] for item in result["satellites"]] == [
        portable_entity,
        kitchen_entity,
    ]
    assert result["satellites"][0]["name"] == portable_entity
    assert [item["id"] for item in result["areas"]] == ["kitchen"]
    assert [item["id"] for item in result["floors"]] == ["ground"]
    assert {item["id"] for item in result["labels"]} == {
        "entity-label",
        "area-label",
        "device-label",
    }
    assert [item["id"] for item in result["devices"]] == ["device-1"]
