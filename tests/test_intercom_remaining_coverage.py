"""Additional behavioral coverage for residual Broadcast/intercom branches."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


class _EntityRegistry:
    def __init__(self, entries: dict[str, object]) -> None:
        self.entries = entries

    def async_get(self, entity_id: str):
        return self.entries.get(entity_id)


class _DeviceRegistry:
    def __init__(self, entries: dict[str, object]) -> None:
        self.entries = entries

    def async_get(self, device_id: str):
        return self.entries.get(device_id)


class _AreaRegistry:
    def __init__(self, entries: dict[str, object]) -> None:
        self.entries = entries

    def async_get_area(self, area_id: str | None):
        return self.entries.get(area_id)

    def async_list_areas(self):
        return list(self.entries.values())


class _ListRegistry:
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

    missing = _message(entity_id, message_id="missing", include_delivery=False)
    delivered = _message(entity_id, message_id="delivered", status="delivered")
    failed = _message(entity_id, message_id="failed", status="failed")
    expired = _message(entity_id, message_id="expired", status="expired")
    pending = _message(entity_id, message_id="pending", status="waiting_idle")
    inflight = _message(entity_id, message_id="inflight", status="delivering")
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

    entities = _EntityRegistry(
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
    devices = _DeviceRegistry({})
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)

    assert manager._entity_area_id("assist_satellite.direct") == "kitchen"
    assert manager._entity_area_id("assist_satellite.device_missing") is None
    assert manager._entity_area_id("assist_satellite.no_device") is None


def test_target_matching_handles_missing_entity_and_missing_device(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    entities = _EntityRegistry(
        {
            "assist_satellite.kitchen": SimpleNamespace(
                area_id=None,
                device_id="missing-device",
                labels=set(),
            )
        }
    )
    devices = _DeviceRegistry({})
    areas = _AreaRegistry({})
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
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: _EntityRegistry({}))
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


@pytest.mark.asyncio
async def test_drain_finally_reschedules_when_busy_satellite_becomes_idle(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message(entity_id, message_id="message")
    manager._queues[entity_id] = deque([item])
    manager._draining.add(entity_id)
    hass.states.get.side_effect = [
        SimpleNamespace(state="responding"),
        SimpleNamespace(state="idle"),
    ]
    scheduled: list[str] = []
    monkeypatch.setattr(manager, "_schedule_drain", scheduled.append)

    await manager._async_drain(entity_id)

    assert item.deliveries[entity_id].status == "queued_busy"
    assert scheduled == [entity_id]
    assert entity_id not in manager._draining


def test_expire_cleans_same_message_entries_without_delivery_or_already_terminal(hass) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    missing = _message(entity_id, message_id="target", include_delivery=False)
    delivered = _message(entity_id, message_id="target", status="delivered")
    unrelated = _message(entity_id, message_id="other", status="queued_busy")
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
    entities = _EntityRegistry(
        {
            kitchen_entity: SimpleNamespace(
                area_id="kitchen",
                device_id="device-1",
                labels={"entity-label"},
                aliases=["Cooker"],
            )
        }
    )
    devices = _DeviceRegistry(
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
    areas = _AreaRegistry(
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
    floors = _ListRegistry(
        [
            SimpleNamespace(
                floor_id="ground", name="Ground Floor", aliases=["Downstairs"]
            ),
            SimpleNamespace(floor_id="upper", name="Upper Floor", aliases=[]),
        ]
    )
    labels = _ListRegistry(
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

    result = manager = IntercomManager(hass).catalog()

    assert [item["id"] for item in result["satellites"]] == [
        kitchen_entity,
        portable_entity,
    ]
    assert result["satellites"][1]["name"] == portable_entity
    assert [item["id"] for item in result["areas"]] == ["kitchen"]
    assert [item["id"] for item in result["floors"]] == ["ground"]
    assert {item["id"] for item in result["labels"]} == {
        "entity-label",
        "area-label",
        "device-label",
    }
    assert [item["id"] for item in result["devices"]] == ["device-1"]
