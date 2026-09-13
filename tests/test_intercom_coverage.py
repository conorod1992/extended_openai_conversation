"""Residual behavioral coverage for Broadcast/intercom routing and delivery."""

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
)


class _EntityRegistry:
    def __init__(self, entries):
        self.entries = entries

    def async_get(self, entity_id):
        return self.entries.get(entity_id)


class _LookupRegistry:
    def __init__(self, entries):
        self.entries = entries

    def async_get(self, item_id):
        return self.entries.get(item_id)

    def async_get_area(self, item_id):
        return self.entries.get(item_id)


def _message(entity_id: str, *, status: str = "queued_busy", seconds: int = 30):
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


@pytest.mark.asyncio
async def test_initialize_loads_persisted_enabled_once(hass) -> None:
    manager = IntercomManager(hass)
    manager._store.async_load = AsyncMock(return_value={"enabled": True})

    await manager.async_initialize()
    await manager.async_initialize()

    assert manager.enabled is True
    manager._store.async_load.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_defaults_disabled_without_storage(hass) -> None:
    manager = IntercomManager(hass)
    manager._store.async_load = AsyncMock(return_value=None)

    await manager.async_initialize()

    assert manager.enabled is False


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
    entities = _EntityRegistry(
        {
            "assist_satellite.kitchen": SimpleNamespace(
                area_id=None, device_id="device-1", labels=set()
            )
        }
    )
    devices = _LookupRegistry({"device-1": SimpleNamespace(area_id="kitchen", labels=set())})
    monkeypatch.setattr(intercom.er, "async_get", lambda _hass: entities)
    monkeypatch.setattr(intercom.dr, "async_get", lambda _hass: devices)

    assert manager._entity_area_id("assist_satellite.kitchen") == "kitchen"
    assert manager._entity_area_id("assist_satellite.missing") is None


def test_target_matching_supports_direct_device_area_floor_and_labels(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    entity = SimpleNamespace(
        device_id="device-1", area_id="kitchen", labels={"entity-label"}
    )
    entities = _EntityRegistry({"assist_satellite.kitchen": entity})
    devices = _LookupRegistry(
        {"device-1": SimpleNamespace(area_id="kitchen", labels={"device-label"})}
    )
    areas = _LookupRegistry(
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
    entries = _EntityRegistry(
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


def test_schedule_drain_is_idempotent(hass, monkeypatch) -> None:
    manager = IntercomManager(hass)
    manager._draining.add("assist_satellite.kitchen")
    create = AsyncMock()
    monkeypatch.setattr(hass, "async_create_task", create)

    manager._schedule_drain("assist_satellite.kitchen")

    create.assert_not_called()


@pytest.mark.asyncio
async def test_drain_marks_expired_when_disabled_or_ttl_elapsed(hass) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = False
    disabled = _message(entity_id)
    manager._queues[entity_id] = deque([disabled])

    await manager._async_drain(entity_id)

    assert disabled.deliveries[entity_id].status == "expired"
    assert disabled.deliveries[entity_id].detail == "broadcast_disabled"

    manager._enabled = True
    expired = _message(entity_id, seconds=-1)
    manager._queues[entity_id] = deque([expired])

    await manager._async_drain(entity_id)

    assert expired.deliveries[entity_id].status == "expired"


@pytest.mark.asyncio
async def test_drain_records_announce_failure(hass, monkeypatch) -> None:
    entity_id = "assist_satellite.kitchen"
    manager = IntercomManager(hass)
    manager._enabled = True
    item = _message(entity_id, status="queued_idle")
    manager._queues[entity_id] = deque([item])
    hass.states.get.return_value = SimpleNamespace(state="idle")
    monkeypatch.setattr(intercom.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        hass.services,
        "async_call",
        AsyncMock(side_effect=RuntimeError("speaker unavailable")),
    )

    await manager._async_drain(entity_id)

    assert item.deliveries[entity_id].status == "failed"
    assert item.deliveries[entity_id].detail == "RuntimeError"
    assert entity_id not in manager._queues


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
