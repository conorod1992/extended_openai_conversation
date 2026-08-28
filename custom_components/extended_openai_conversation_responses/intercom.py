"""Targeted Assist Satellite announcements with non-interrupting delivery."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import re
from typing import Any
from uuid import uuid4

from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_SUPPORTED_FEATURES
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    floor_registry as fr,
    label_registry as lr,
)
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

DOMAIN = "extended_openai_conversation_responses"
DATA_KEY = f"{DOMAIN}.intercom"
HISTORY_LIMIT = 50
DEFAULT_TTL_SECONDS = 120
IDLE_STABILITY_SECONDS = 0.5


@dataclass(slots=True)
class Delivery:
    entity_id: str
    status: str = "pending"
    detail: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def set(self, status: str, detail: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.updated_at = datetime.now(UTC).isoformat()


@dataclass(slots=True)
class BroadcastMessage:
    id: str
    message: str
    created_at: str
    expires_at: datetime
    source: str
    origin_entity_id: str | None
    origin_device_id: str | None
    targets: list[str]
    deliveries: dict[str, Delivery]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message": self.message,
            "created_at": self.created_at,
            "expires_at": self.expires_at.isoformat(),
            "source": self.source,
            "origin_entity_id": self.origin_entity_id,
            "origin_device_id": self.origin_device_id,
            "targets": list(self.targets),
            "deliveries": {
                entity_id: {
                    "status": delivery.status,
                    "detail": delivery.detail,
                    "updated_at": delivery.updated_at,
                }
                for entity_id, delivery in self.deliveries.items()
            },
        }


class IntercomManager:
    """Resolve destinations and serialize announcements per Assist satellite."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._history: deque[BroadcastMessage] = deque(maxlen=HISTORY_LIMIT)
        self._queues: dict[str, deque[BroadcastMessage]] = {}
        self._draining: set[str] = set()
        self._unsub_state = async_track_state_change_event(
            hass, self._satellite_entity_ids(), self._async_state_changed
        )

    def _satellite_entity_ids(self) -> list[str]:
        return [state.entity_id for state in self.hass.states.async_all("assist_satellite")]

    def _state_announce_capable(self, state: State | None) -> bool:
        if state is None:
            return False
        features = int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) or 0)
        return bool(features & AssistSatelliteEntityFeature.ANNOUNCE)

    def _entity_area_id(self, entity_id: str) -> str | None:
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity is None:
            return None
        if entity.area_id:
            return entity.area_id
        if entity.device_id:
            device = dr.async_get(self.hass).async_get(entity.device_id)
            if device is not None:
                return device.area_id
        return None

    def _target_matches(
        self,
        entity_id: str,
        *,
        entity_ids: set[str],
        device_ids: set[str],
        area_ids: set[str],
        floor_ids: set[str],
        label_ids: set[str],
    ) -> bool:
        entity = er.async_get(self.hass).async_get(entity_id)
        if entity_id in entity_ids:
            return True
        if entity is None:
            return False
        if entity.device_id and entity.device_id in device_ids:
            return True
        area_id = self._entity_area_id(entity_id)
        if area_id and area_id in area_ids:
            return True
        area = ar.async_get(self.hass).async_get_area(area_id) if area_id else None
        if area and area.floor_id and area.floor_id in floor_ids:
            return True
        labels = set(entity.labels)
        if area:
            labels.update(area.labels)
        if entity.device_id:
            device = dr.async_get(self.hass).async_get(entity.device_id)
            if device:
                labels.update(device.labels)
        return bool(labels & label_ids)

    def resolve_targets(
        self,
        *,
        whole_home: bool = False,
        entity_ids: list[str] | None = None,
        device_ids: list[str] | None = None,
        area_ids: list[str] | None = None,
        floor_ids: list[str] | None = None,
        label_ids: list[str] | None = None,
        origin_entity_id: str | None = None,
    ) -> list[str]:
        entities = set(entity_ids or [])
        devices = set(device_ids or [])
        areas = set(area_ids or [])
        floors = set(floor_ids or [])
        labels = set(label_ids or [])
        result = []
        for state in self.hass.states.async_all("assist_satellite"):
            if state.entity_id == origin_entity_id:
                continue
            if not self._state_announce_capable(state):
                continue
            if whole_home or self._target_matches(
                state.entity_id,
                entity_ids=entities,
                device_ids=devices,
                area_ids=areas,
                floor_ids=floors,
                label_ids=labels,
            ):
                result.append(state.entity_id)
        return sorted(set(result))

    async def async_send(
        self,
        message: str,
        *,
        whole_home: bool = False,
        entity_ids: list[str] | None = None,
        device_ids: list[str] | None = None,
        area_ids: list[str] | None = None,
        floor_ids: list[str] | None = None,
        label_ids: list[str] | None = None,
        origin_entity_id: str | None = None,
        origin_device_id: str | None = None,
        source: str = "manual",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        text = message.strip()
        if not text:
            raise HomeAssistantError("Broadcast message cannot be empty")
        targets = self.resolve_targets(
            whole_home=whole_home,
            entity_ids=entity_ids,
            device_ids=device_ids,
            area_ids=area_ids,
            floor_ids=floor_ids,
            label_ids=label_ids,
            origin_entity_id=origin_entity_id,
        )
        if not targets:
            raise HomeAssistantError("No matching announcement-capable Assist satellites found")
        now = datetime.now(UTC)
        item = BroadcastMessage(
            id=uuid4().hex,
            message=text,
            created_at=now.isoformat(),
            expires_at=now + timedelta(seconds=max(5, min(ttl_seconds, 3600))),
            source=source,
            origin_entity_id=origin_entity_id,
            origin_device_id=origin_device_id,
            targets=targets,
            deliveries={entity_id: Delivery(entity_id) for entity_id in targets},
        )
        self._history.appendleft(item)
        for entity_id in targets:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == "idle":
                item.deliveries[entity_id].set("queued_idle")
            else:
                item.deliveries[entity_id].set("queued_busy")
            self._queues.setdefault(entity_id, deque()).append(item)
            self._schedule_drain(entity_id)
        async_call_later(
            self.hass,
            max(5, min(ttl_seconds, 3600)),
            lambda _now, message_id=item.id: self._expire(message_id),
        )
        return item.as_dict()

    @callback
    def _schedule_drain(self, entity_id: str) -> None:
        if entity_id in self._draining:
            return
        self._draining.add(entity_id)
        self.hass.async_create_task(self._async_drain(entity_id))

    async def _async_drain(self, entity_id: str) -> None:
        try:
            queue = self._queues.get(entity_id)
            while queue:
                item = queue[0]
                delivery = item.deliveries[entity_id]
                if datetime.now(UTC) >= item.expires_at:
                    delivery.set("expired")
                    queue.popleft()
                    continue
                state = self.hass.states.get(entity_id)
                if state is None or state.state != "idle":
                    delivery.set("queued_busy")
                    return
                delivery.set("waiting_idle")
                await asyncio.sleep(IDLE_STABILITY_SECONDS)
                state = self.hass.states.get(entity_id)
                if state is None or state.state != "idle":
                    delivery.set("queued_busy")
                    return
                delivery.set("delivering")
                try:
                    await self.hass.services.async_call(
                        "assist_satellite",
                        "announce",
                        {"message": item.message},
                        target={"entity_id": entity_id},
                        blocking=True,
                    )
                except Exception as err:
                    delivery.set("failed", type(err).__name__)
                else:
                    delivery.set("delivered")
                queue.popleft()
            if queue is not None and not queue:
                self._queues.pop(entity_id, None)
        finally:
            self._draining.discard(entity_id)

    @callback
    def _async_state_changed(self, event: Event[Any]) -> None:
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        if entity_id in self._queues and new_state is not None and new_state.state == "idle":
            self._schedule_drain(entity_id)

    @callback
    def _expire(self, message_id: str) -> None:
        for item in self._history:
            if item.id != message_id:
                continue
            for entity_id, delivery in item.deliveries.items():
                if delivery.status in {"delivered", "failed", "expired"}:
                    continue
                delivery.set("expired")
                queue = self._queues.get(entity_id)
                if queue:
                    self._queues[entity_id] = deque(m for m in queue if m.id != message_id)
                    if not self._queues[entity_id]:
                        self._queues.pop(entity_id, None)
            break

    def history(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self._history]

    def catalog(self) -> dict[str, Any]:
        areas = ar.async_get(self.hass)
        floors = fr.async_get(self.hass)
        labels = lr.async_get(self.hass)
        devices = dr.async_get(self.hass)
        satellites = []
        relevant_area_ids: set[str] = set()
        relevant_device_ids: set[str] = set()
        relevant_label_ids: set[str] = set()
        relevant_floor_ids: set[str] = set()
        for state in self.hass.states.async_all("assist_satellite"):
            if not self._state_announce_capable(state):
                continue
            entity = er.async_get(self.hass).async_get(state.entity_id)
            area_id = self._entity_area_id(state.entity_id)
            device_id = entity.device_id if entity else None
            area = areas.async_get_area(area_id) if area_id else None
            if area_id:
                relevant_area_ids.add(area_id)
            if device_id:
                relevant_device_ids.add(device_id)
            if area and area.floor_id:
                relevant_floor_ids.add(area.floor_id)
            if entity:
                relevant_label_ids.update(entity.labels)
            if area:
                relevant_label_ids.update(area.labels)
            if device_id and (device := devices.async_get(device_id)):
                relevant_label_ids.update(device.labels)
            satellites.append({
                "id": state.entity_id,
                "name": state.attributes.get(ATTR_FRIENDLY_NAME, state.entity_id),
                "state": state.state,
                "area_id": area_id,
                "device_id": device_id,
            })
        return {
            "satellites": sorted(satellites, key=lambda x: str(x["name"]).casefold()),
            "areas": sorted(
                [{"id": item.id, "name": item.name} for item in areas.async_list_areas() if item.id in relevant_area_ids],
                key=lambda x: x["name"].casefold(),
            ),
            "floors": sorted(
                [{"id": item.floor_id, "name": item.name} for item in floors.async_list_floors() if item.floor_id in relevant_floor_ids],
                key=lambda x: x["name"].casefold(),
            ),
            "labels": sorted(
                [{"id": item.label_id, "name": item.name} for item in labels.async_list_labels() if item.label_id in relevant_label_ids],
                key=lambda x: x["name"].casefold(),
            ),
            "devices": sorted(
                [
                    {"id": item.id, "name": item.name_by_user or item.name}
                    for item in (devices.async_get(device_id) for device_id in relevant_device_ids)
                    if item is not None
                ],
                key=lambda x: str(x["name"]).casefold(),
            ),
        }

    def resolve_named_target(self, name: str) -> dict[str, Any] | None:
        wanted = re.sub(r"\s+", " ", name.strip().casefold())
        catalog = self.catalog()
        aliases = {"everyone", "everywhere", "whole home", "the whole house", "all devices", "all speakers"}
        if wanted in aliases:
            return {"whole_home": True, "name": name.strip()}
        singular = {"satellites": "entity_ids", "devices": "device_ids", "areas": "area_ids", "floors": "floor_ids", "labels": "label_ids"}
        for group, field in singular.items():
            for item in catalog[group]:
                item_name = re.sub(r"\s+", " ", str(item["name"]).casefold())
                if wanted in {item_name, f"the {item_name}"}:
                    return {field: [item["id"]], "name": item["name"]}
        return None


async def async_get_intercom(hass: HomeAssistant) -> IntercomManager:
    manager = hass.data.get(DATA_KEY)
    if manager is None:
        manager = IntercomManager(hass)
        hass.data[DATA_KEY] = manager
    return manager


def parse_targeted_broadcast(text: str, manager: IntercomManager) -> tuple[dict[str, Any], str] | None:
    """Parse deliberately explicit targeted broadcast wording without an LLM call."""
    value = re.sub(r"\s+", " ", text.strip())
    patterns = (
        r"^(?:broadcast|announce|send)(?: a message)? to (?P<rest>.+)$",
        r"^tell (?P<rest>.+)$",
    )
    rest = None
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match:
            rest = match.group("rest")
            break
    if rest is None:
        return None
    catalog = manager.catalog()
    candidates = [
        *(str(item["name"]) for group in ("satellites", "devices", "areas", "floors", "labels") for item in catalog[group]),
        "everyone", "everywhere", "whole home", "the whole house", "all devices", "all speakers",
    ]
    for candidate in sorted(set(candidates), key=len, reverse=True):
        target = manager.resolve_named_target(candidate)
        if target is None:
            continue
        prefix = re.escape(candidate)
        payload_match = re.match(
            rf"^(?:the )?{prefix}(?:\s+(?:that|saying|message)\s+|[:,]\s*|\s+)(?P<message>.+)$",
            rest,
            flags=re.IGNORECASE,
        )
        if payload_match:
            payload = payload_match.group("message").strip()
            target.pop("name", None)
            return target, payload
    return None
