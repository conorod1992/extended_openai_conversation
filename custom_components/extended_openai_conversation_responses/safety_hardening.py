"""Focused authorization and resource guards for model-facing runtime paths."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.util.dt as dt_util

from .ha_permissions import get_active_ha_context
from .resource_limits import MAX_NATIVE_SERVICE_ACTIONS

MAX_HISTORY_ENTITY_IDS = 50
MAX_HISTORY_SPAN = timedelta(days=31)
MAX_STATISTIC_SPAN_BY_PERIOD: dict[str, timedelta] = {
    "5minute": timedelta(days=7),
    "hour": timedelta(days=90),
    "day": timedelta(days=366 * 5),
    "month": timedelta(days=366 * 20),
}


async def _async_require_admin(hass: HomeAssistant, llm_context: Any) -> None:
    """Require an active authenticated HA administrator for durable HA mutation."""
    context = getattr(llm_context, "context", None) or get_active_ha_context()
    user_id = getattr(context, "user_id", None)
    if not isinstance(user_id, str) or not user_id:
        raise HomeAssistantError(
            "add_automation requires an authenticated Home Assistant administrator"
        )
    user = await hass.auth.async_get_user(user_id)
    if (
        user is None
        or getattr(user, "is_active", True) is not True
        or getattr(user, "is_admin", False) is not True
    ):
        raise HomeAssistantError(
            "add_automation requires an active Home Assistant administrator"
        )


def _parse_datetime(value: Any, label: str) -> Any:
    """Parse one required ISO datetime to UTC."""
    if not isinstance(value, str):
        raise HomeAssistantError(f"{label} must be an ISO 8601 datetime")
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise HomeAssistantError(f"{label} must be an ISO 8601 datetime")
    return dt_util.as_utc(parsed)


def _validate_time_window(start: Any, end: Any, maximum: timedelta) -> None:
    """Reject inverted and excessively expensive Recorder windows."""
    if end <= start:
        raise HomeAssistantError("end_time must be after start_time")
    if end - start > maximum:
        raise HomeAssistantError(
            f"Requested Recorder time range may not exceed {maximum.days} days"
        )


def _validate_history_request(arguments: dict[str, Any]) -> None:
    """Bound history cardinality and time before Recorder materializes results."""
    entity_ids = arguments.get("entity_ids")
    if (
        not isinstance(entity_ids, list)
        or not entity_ids
        or any(
            not isinstance(entity_id, str) or not entity_id for entity_id in entity_ids
        )
    ):
        raise HomeAssistantError("entity_ids must be a non-empty list of entity IDs")
    if len(entity_ids) > MAX_HISTORY_ENTITY_IDS:
        raise HomeAssistantError(
            f"entity_ids may contain at most {MAX_HISTORY_ENTITY_IDS} IDs"
        )

    now = dt_util.utcnow()
    raw_start = arguments.get("start_time")
    start = (
        _parse_datetime(raw_start, "start_time")
        if raw_start is not None
        else now - timedelta(days=1)
    )
    raw_end = arguments.get("end_time")
    end = (
        _parse_datetime(raw_end, "end_time")
        if raw_end is not None
        else start + timedelta(days=1)
    )
    _validate_time_window(start, end, MAX_HISTORY_SPAN)


def _validate_execute_service_request(arguments: dict[str, Any]) -> None:
    """Bound the number of Home Assistant actions inside one native tool call."""
    actions = arguments.get("list")
    if not isinstance(actions, list):
        raise HomeAssistantError("execute_service list must be an array")
    if len(actions) > MAX_NATIVE_SERVICE_ACTIONS:
        raise HomeAssistantError(
            f"execute_service may contain at most {MAX_NATIVE_SERVICE_ACTIONS} actions"
        )


def _normalized_statistics_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate the period/window and normalize the optional period default."""
    normalized = dict(arguments)
    period = normalized.get("period") or "day"
    if period not in MAX_STATISTIC_SPAN_BY_PERIOD:
        raise HomeAssistantError("Unsupported statistics period")
    normalized["period"] = period
    start = _parse_datetime(normalized.get("start_time"), "start_time")
    end = _parse_datetime(normalized.get("end_time"), "end_time")
    _validate_time_window(start, end, MAX_STATISTIC_SPAN_BY_PERIOD[period])
    return normalized
