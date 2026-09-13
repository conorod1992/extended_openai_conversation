"""Race and catalog coverage for Broadcast intercom behavior."""

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
