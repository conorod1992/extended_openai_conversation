"""Authenticated WebSocket API for the Broadcast frontend."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .ha_permissions import async_require_control_permission
from .intercom import async_get_intercom

WS_BROADCAST = f"{DOMAIN}/broadcast"
_SETUP_KEY = f"{DOMAIN}.broadcast_api_setup"


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_BROADCAST,
        vol.Required("action"): vol.In(["snapshot", "send", "set_enabled"]),
        vol.Optional("message"): str,
        vol.Optional("whole_home"): bool,
        vol.Optional("entity_ids"): [str],
        vol.Optional("enabled"): bool,
    }
)
@websocket_api.async_response
async def websocket_broadcast(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        manager = await async_get_intercom(hass)
        if msg["action"] == "snapshot":
            connection.send_result(
                msg["id"],
                {
                    "enabled": manager.enabled,
                    "can_manage": connection.user.is_admin,
                    "catalog": manager.catalog(),
                    "history": manager.history(),
                },
            )
            return
        if msg["action"] == "set_enabled":
            if not connection.user.is_admin:
                raise HomeAssistantError(
                    "Administrator permission is required to change Broadcast settings"
                )
            enabled = msg.get("enabled")
            if not isinstance(enabled, bool):
                raise HomeAssistantError("enabled must be true or false")
            await manager.async_set_enabled(enabled)
            connection.send_result(msg["id"], {"enabled": manager.enabled})
            return
        message = str(msg.get("message", "")).strip()
        if not message:
            raise HomeAssistantError("Message cannot be empty")
        whole_home = msg.get("whole_home") is True
        entity_ids = [str(item) for item in msg.get("entity_ids", [])]
        if not whole_home and not entity_ids:
            raise HomeAssistantError(
                "Choose at least one Assist satellite or Whole home"
            )

        # Resolve selectors before authorization and queue only that fixed entity
        # list. This prevents a restricted HA user from escalating through the
        # integration's later system-context announce call or selector expansion.
        targets = manager.resolve_targets(
            whole_home=whole_home,
            entity_ids=entity_ids,
        )
        if not targets:
            raise HomeAssistantError(
                "No matching announcement-capable Assist satellites found"
            )
        await async_require_control_permission(
            hass,
            targets,
            context=connection.context(msg),
        )
        result = await manager.async_send(
            message,
            entity_ids=targets,
            source="frontend",
        )
        connection.send_result(msg["id"], result)
    except (HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))


async def async_setup_broadcast_api(hass: HomeAssistant) -> None:
    """Register the typed Broadcast API once."""
    if hass.data.get(_SETUP_KEY):
        return
    hass.data[_SETUP_KEY] = True
    websocket_api.async_register_command(hass, websocket_broadcast)
