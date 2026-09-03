"""Authorization boundaries for agent-global management surfaces."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui

_PATCHED = "extended_openai_management_permissions"
_OPTIMIZED_OVERVIEW_PATCHED = "extended_openai_management_overview_permissions"
ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
]


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise HomeAssistantError("Administrator permission is required")


def sanitize_non_admin_overview(result: dict[str, Any]) -> dict[str, Any]:
    """Remove agent-global run metadata while retaining aggregate overview usage."""
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return result
    return {**result, "usage": {**usage, "latest": None}}


def wrap_management_permissions(original: ManagementCommand) -> ManagementCommand:
    """Protect agent-global data and paid diagnostics from normal HA users."""

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        section = message.get("section", "overview")
        action = message.get("action")

        # Knowledge sources belong to the agent rather than an individual user.
        # Reading them can expose private reference material, while writes affect
        # every user of the agent, so fail closed for the whole section.
        if section == "knowledge":
            _require_admin(is_admin)

        # Diagnostics can issue real provider requests using the owner's account.
        # Keep the whole section admin-only so future diagnostics inherit the same
        # boundary instead of needing an action-by-action allow-list.
        if section == "diagnostics":
            _require_admin(is_admin)

        # Usage details are also agent-global. Keep the harmless aggregate summary
        # available for Overview, but never expose the latest run metadata or any
        # detailed usage endpoint to a normal HA user.
        if section == "usage" and not is_admin:
            if action != "summary":
                _require_admin(False)
            result = await original(hass, user_id, is_admin, message)
            return {**result, "latest": None}

        # The optimized management dispatcher has a combined Overview endpoint.
        # Sanitize it here too when this wrapper is outermost.
        if section == "overview" and action == "summary" and not is_admin:
            return sanitize_non_admin_overview(
                await original(hass, user_id, is_admin, message)
            )

        return await original(hass, user_id, is_admin, message)

    return wrapped


def _install_optimized_overview_guard() -> None:
    """Keep the optimized Overview safe regardless of monkey-patch install order."""
    from . import management_loading_performance

    if getattr(management_loading_performance, _OPTIMIZED_OVERVIEW_PATCHED, False):
        return
    original = management_loading_performance.async_overview_summary

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        result = await original(hass, user_id, is_admin, message)
        return result if is_admin else sanitize_non_admin_overview(result)

    management_loading_performance.async_overview_summary = wrapped  # type: ignore[assignment]
    setattr(management_loading_performance, _OPTIMIZED_OVERVIEW_PATCHED, True)


def install_management_permissions() -> bool:
    """Install the management authorization wrapper once."""
    _install_optimized_overview_guard()
    if getattr(management_ui, _PATCHED, False):
        return False
    management_ui.async_management_command = wrap_management_permissions(  # type: ignore[assignment]
        management_ui.async_management_command
    )
    setattr(management_ui, _PATCHED, True)
    return True
