"""Authorization boundaries for agent-global management surfaces."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .quiet_hours import async_get_quiet_hours


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise HomeAssistantError("Administrator permission is required")


async def async_quiet_hours_command(
    hass: HomeAssistant, is_admin: bool, message: dict[str, Any]
) -> dict[str, Any]:
    """Handle the domain-global Quiet Hours management surface."""
    _require_admin(is_admin)
    manager = await async_get_quiet_hours(hass)
    action = message.get("action")
    if action in {"get", "discover"}:
        return manager.snapshot()
    if action == "update":
        config = message.get("config")
        if not isinstance(config, dict):
            raise HomeAssistantError("config must be an object")
        try:
            return await manager.async_update_config(config)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
    raise HomeAssistantError(f"Unknown Quiet Hours action: {action}")


def require_management_permission(is_admin: bool, message: dict[str, Any]) -> None:
    """Reject agent-global reads before agent lookup or feature-specific validation."""
    section = message.get("section", "overview")
    if section in {"quiet_hours", "knowledge", "diagnostics", "function_repair"}:
        _require_admin(is_admin)
    if section == "request_rules" and message.get("action") in {"test", "test_match"}:
        _require_admin(is_admin)
    if section == "configuration" and message.get("action") == "save":
        _require_admin(is_admin)
    if section == "usage" and message.get("action") != "summary":
        _require_admin(is_admin)
