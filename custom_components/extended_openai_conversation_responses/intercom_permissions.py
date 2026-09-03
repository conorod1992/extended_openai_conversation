"""Permission-safe target resolution for Broadcast announcements."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .ha_permissions import async_require_control_permission


async def async_authorized_broadcast_targets(
    hass: HomeAssistant,
    manager: Any,
    *,
    context: Context | None,
    whole_home: bool = False,
    entity_ids: Iterable[str] | None = None,
    device_ids: Iterable[str] | None = None,
    area_ids: Iterable[str] | None = None,
    floor_ids: Iterable[str] | None = None,
    label_ids: Iterable[str] | None = None,
    origin_entity_id: str | None = None,
    origin_device_id: str | None = None,
) -> list[str]:
    """Resolve selectors once and require CONTROL permission for every target."""
    resolved = manager.resolve_targets(
        whole_home=whole_home,
        entity_ids=list(entity_ids or []),
        device_ids=list(device_ids or []),
        area_ids=list(area_ids or []),
        floor_ids=list(floor_ids or []),
        label_ids=list(label_ids or []),
        origin_entity_id=origin_entity_id,
        origin_device_id=origin_device_id,
    )
    targets = [str(entity_id) for entity_id in resolved]
    if not targets:
        raise HomeAssistantError(
            "No matching announcement-capable Assist satellites found"
        )
    await async_require_control_permission(hass, targets, context=context)
    return targets
