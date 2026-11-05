"""Authoritative HA-local dates for Management, independent of client TZDB."""

from datetime import UTC, datetime
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util


def local_wall_time(hass: HomeAssistant, value: Any) -> str:
    """Project an existing instant using the server's configured timezone."""
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    zone = dt_util.get_time_zone(hass.config.time_zone)
    if parsed is None or parsed.tzinfo is None or zone is None:
        return ""
    return parsed.astimezone(zone).isoformat(timespec="seconds")[:19]


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
    candidates = set()
    try:
        for fold in (0, 1):
            instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None) == wall:
                candidates.add(instant)
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
        result = {
            **result,
            "status": {
                **status,
                **{
                    field + "_local": local_wall_time(hass, status.get(field))[:16]
                    for field in ("active_from", "active_until")
                    if field in status
                },
            },
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
