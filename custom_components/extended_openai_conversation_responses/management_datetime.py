"""Authoritative HA-local dates for Management, independent of client TZDB."""

from datetime import UTC, datetime, tzinfo
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .local_time import wall_time_instants
from .record_timestamps import parse_datetime


def local_wall_time(hass: HomeAssistant, value: Any) -> str | None:
    """Project an existing instant using the server's configured timezone."""
    return _wall_time(value, dt_util.get_time_zone(hass.config.time_zone))


def _wall_time(value: Any, zone: tzinfo | None) -> str | None:
    parsed = parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None or zone is None:
        return ""
    try:
        return parsed.astimezone(zone).isoformat(timespec="seconds")[:19]
    except OverflowError:
        # The instant remains valid in UTC even if its local year is outside
        # datetime's range. Keep the canonical value in the response.
        return None


def resolve_local_time(
    hass: HomeAssistant, value: Any, *, reject_ambiguous: bool
) -> str:
    """Reject gaps; preserve the UI's first-fold/reject-fold policies."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", value
    ):
        raise HomeAssistantError("Choose a valid local date and time.")
    try:
        wall = datetime.fromisoformat(value)
    except ValueError as err:
        raise HomeAssistantError("Choose a valid local date and time.") from err
    zone = dt_util.get_time_zone(hass.config.time_zone)
    if zone is None:
        raise HomeAssistantError("Home Assistant timezone is unavailable")
    try:
        candidates = wall_time_instants(wall, zone)
    except (OverflowError, ValueError) as err:
        raise HomeAssistantError("Choose a valid local date and time.") from err
    if not candidates:
        raise HomeAssistantError(
            "This time does not exist in Home Assistant's timezone."
        )
    if reject_ambiguous and len(candidates) > 1:
        raise HomeAssistantError(
            "This time occurs twice in Home Assistant's timezone. Choose an unambiguous time."
        )
    return min(candidates).isoformat()


def resolve_management_dates(
    hass: HomeAssistant, message: dict[str, Any]
) -> dict[str, Any]:
    """Interpret only explicitly marked UI wall times, leaving legacy ISO intact."""
    if "local_times" not in message:
        return message
    section, action = message.get("section"), message.get("action")
    allowed = (
        {"active_from", "active_until"}
        if section == "guest_mode" and action == "update"
        else {"expires_at"}
        if section == "memories" and action in {"temporary_add", "temporary_update"}
        else set()
    )
    local = message["local_times"]
    if not isinstance(local, dict) or not allowed or set(local) - allowed:
        raise HomeAssistantError("Invalid local date fields")
    result = dict(message)
    for field, value in local.items():
        if value != message.get(field):
            raise HomeAssistantError("Local date does not match the submitted field")
        result[field] = resolve_local_time(
            hass, value, reject_ambiguous=section == "guest_mode"
        )
    return result


def project_management_dates(
    hass: HomeAssistant, section: str, result: dict[str, Any]
) -> dict[str, Any]:
    """Return editor wall times from the same rules that resolve their saves."""
    if section == "guest_mode" and isinstance(result.get("status"), dict):
        status = result["status"]
        local_times = {}
        for field in ("active_from", "active_until"):
            if field in status:
                wall_time = local_wall_time(hass, status[field])
                local_times[field + "_local"] = (
                    wall_time[:16] if wall_time is not None else None
                )
                if wall_time is None:
                    local_times[field + "_utc"] = _wall_time(status[field], UTC)
        result = {
            **result,
            "status": {**status, **local_times},
        }
    if section == "memories":

        def project(record: Any) -> Any:
            return (
                {
                    **record,
                    "expires_at_local": local_wall_time(hass, record["expires_at"]),
                }
                if isinstance(record, dict) and "expires_at" in record
                else record
            )

        result = dict(result)
        if isinstance(result.get("memories"), list):
            result["memories"] = [project(record) for record in result["memories"]]
        if isinstance(result.get("memory"), dict):
            result["memory"] = project(result["memory"])
    return result
