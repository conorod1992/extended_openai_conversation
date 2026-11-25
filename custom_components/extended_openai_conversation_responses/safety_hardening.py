"""Focused authorization and resource guards for model-facing runtime paths."""

from __future__ import annotations

from datetime import MAXYEAR, MINYEAR, date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.util.dt as dt_util

from .ha_permissions import get_active_ha_context
from .record_timestamps import parse_datetime
from .resource_limits import MAX_NATIVE_SERVICE_ACTIONS

MAX_HISTORY_ENTITY_IDS = 50
MAX_HISTORY_SPAN = timedelta(days=31)
MAX_STATISTIC_SPAN_BY_PERIOD: dict[str, timedelta] = {
    "5minute": timedelta(days=7),
    "hour": timedelta(days=90),
    "day": timedelta(days=366 * 5),
    "week": timedelta(days=366 * 20),
    "month": timedelta(days=366 * 20),
    "year": timedelta(days=366 * 100),
}
# Recorder extends the local end time to the start of the next calendar period.
_LATEST_STATISTICS_LOCAL_END = {
    "day": date(MAXYEAR, 12, 30),
    "week": date.max - timedelta(days=date.max.weekday() + 1),
    "month": date(MAXYEAR, 11, 30),
    "year": date(MAXYEAR - 1, 12, 31),
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


def _parse_datetime(value: Any, label: str) -> datetime:
    """Parse one required ISO datetime to UTC."""
    if not isinstance(value, str):
        raise HomeAssistantError(f"{label} must be an ISO 8601 datetime")
    try:
        parsed = parse_datetime(value)
        if parsed is not None:
            return dt_util.as_utc(parsed)
    except (ValueError, OverflowError) as err:
        raise HomeAssistantError(f"{label} must be an ISO 8601 datetime") from err
    raise HomeAssistantError(f"{label} must be an ISO 8601 datetime")


def _offset_datetime(value: datetime, offset: timedelta, label: str) -> datetime:
    try:
        return value + offset
    except OverflowError as err:
        raise HomeAssistantError(
            f"Default {label} is outside the supported date range"
        ) from err


def _validate_time_window(start: datetime, end: datetime, maximum: timedelta) -> None:
    """Reject inverted and excessively expensive Recorder windows."""
    if end <= start:
        raise HomeAssistantError("end_time must be after start_time")
    if end - start > maximum:
        raise HomeAssistantError(
            f"Requested Recorder time range may not exceed {maximum.days} days"
        )


def _validate_history_request(arguments: dict[str, Any]) -> tuple[datetime, datetime]:
    """Bound history cardinality and return the effective UTC Recorder window."""
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

    one_day = timedelta(days=1)
    raw_start = arguments.get("start_time")
    start = (
        _parse_datetime(raw_start, "start_time")
        if raw_start is not None
        else _offset_datetime(dt_util.utcnow(), -one_day, "start_time")
    )
    raw_end = arguments.get("end_time")
    end = (
        _parse_datetime(raw_end, "end_time")
        if raw_end is not None
        else _offset_datetime(start, one_day, "end_time")
    )
    _validate_time_window(start, end, MAX_HISTORY_SPAN)
    return start, end


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
    """Prepare the period and the UTC window for Recorder statistics."""
    normalized = dict(arguments)
    period = normalized.get("period") or "day"
    if period not in MAX_STATISTIC_SPAN_BY_PERIOD:
        raise HomeAssistantError("Unsupported statistics period")
    normalized["period"] = period
    start = _parse_datetime(normalized.get("start_time"), "start_time")
    end = _parse_datetime(normalized.get("end_time"), "end_time")
    _validate_time_window(start, end, MAX_STATISTIC_SPAN_BY_PERIOD[period])
    _validate_statistics_calendar_bounds(start, end, period)
    normalized.update(start_time=start, end_time=end)
    return normalized


def _validate_statistics_calendar_bounds(
    start: datetime, end: datetime, period: str
) -> None:
    """Reject ranges whose Recorder period cannot be aligned in HA's timezone."""
    latest_end = _LATEST_STATISTICS_LOCAL_END.get(period)
    if latest_end is None or (start.year > MINYEAR and end.year < MAXYEAR - 1):
        return
    try:
        dt_util.as_local(start)
        local_end = dt_util.as_local(end)
    except (ValueError, OverflowError) as err:
        raise HomeAssistantError(
            "Recorder time range is outside the supported dates in Home Assistant's timezone"
        ) from err
    if local_end.date() > latest_end:
        raise HomeAssistantError(
            f"end_time is outside the supported date range for {period} statistics"
        )
