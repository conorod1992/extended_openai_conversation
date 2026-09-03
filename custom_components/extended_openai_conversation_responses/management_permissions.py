"""Authorization boundaries for agent-global management surfaces."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui

_PATCHED = "extended_openai_management_permissions"
ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
]


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise HomeAssistantError("Administrator permission is required")


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

        return await original(hass, user_id, is_admin, message)

    return wrapped


def install_management_permissions() -> bool:
    """Install the management authorization wrapper once."""
    if getattr(management_ui, _PATCHED, False):
        return False
    management_ui.async_management_command = wrap_management_permissions(  # type: ignore[method-assign]
        management_ui.async_management_command
    )
    setattr(management_ui, _PATCHED, True)
    return True
