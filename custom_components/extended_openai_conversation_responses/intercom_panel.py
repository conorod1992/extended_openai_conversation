"""Authenticated WebSocket API for the Broadcast frontend."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .ha_schema import websocket_command as ha_websocket_command
from .intercom import async_get_intercom
from .intercom_permissions import async_authorized_broadcast_targets

WS_BROADCAST = f"{DOMAIN}/broadcast"
_SETUP_KEY = f"{DOMAIN}.broadcast_api_setup"


@ha_websocket_command(
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
            catalog, history = manager.catalog(), manager.history()
            if not connection.user.is_admin:

                def can_read(entity_id: str) -> bool:
                    return connection.user.permissions.check_entity(
                        entity_id, POLICY_READ
                    )

                readable = {
                    item["id"] for item in catalog["satellites"] if can_read(item["id"])
                }
                catalog = manager.catalog(entity_ids=readable)
                history = [
                    {**item, "origin_device_id": None}
                    for item in history
                    if item.get("targets")
                    and all(can_read(entity_id) for entity_id in item["targets"])
                    and all(
                        can_read(entity_id) for entity_id in item.get("deliveries", {})
                    )
                    and (
                        not item.get("origin_entity_id")
                        or can_read(item["origin_entity_id"])
                    )
                ]
            connection.send_result(
                msg["id"],
                {
                    "enabled": manager.enabled,
                    "can_manage": connection.user.is_admin,
                    "catalog": catalog,
                    "history": history,
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

        # Queue only the exact targets that were resolved and authorized for this
        # authenticated Home Assistant caller.
        targets = await async_authorized_broadcast_targets(
            hass,
            manager,
            context=connection.context(msg),
            whole_home=whole_home,
            entity_ids=entity_ids,
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
