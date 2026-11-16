"""Validate the exact indirect IDs that Home Assistant will dispatch."""

from collections.abc import Mapping
from typing import Any

from .public_errors import InvalidIndirectTarget

INDIRECT_TARGET_KEYS = ("area_id", "device_id", "floor_id", "label_id")


def validate_indirect_targets(
    data: Mapping[str, Any] | None, target: Mapping[str, Any] | None = None
) -> None:
    """Check effective selectors without changing payloads or empty targets.

    HA overlays target onto data, including None and empty lists. Do not inspect
    an overridden value, entity_id, or similarly named nested service data.
    """
    merged = {**(data or {}), **(target or {})}
    for key in INDIRECT_TARGET_KEYS:
        value = merged.get(key)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        if any(
            not isinstance(item, str) or not item or item != item.strip() or "," in item
            for item in items
        ):
            raise InvalidIndirectTarget()
