"""Small authenticated frontend/API for sending typed intercom messages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .intercom import async_get_intercom

WS_INTERCOM = f"{DOMAIN}/intercom"
PANEL_URL = "extended-openai-intercom"
PANEL_TITLE = "Intercom"
_SETUP_KEY = f"{DOMAIN}.intercom_panel_setup"


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_INTERCOM,
        vol.Required("action"): vol.In(["snapshot", "send"]),
        vol.Optional("message"): str,
        vol.Optional("whole_home"): bool,
        vol.Optional("entity_ids"): [str],
    }
)
@websocket_api.async_response
async def websocket_intercom(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        manager = await async_get_intercom(hass)
        if msg["action"] == "snapshot":
            connection.send_result(
                msg["id"],
                {"catalog": manager.catalog(), "history": manager.history()},
            )
            return
        message = str(msg.get("message", "")).strip()
        if not message:
            raise HomeAssistantError("Message cannot be empty")
        whole_home = msg.get("whole_home") is True
        entity_ids = [str(item) for item in msg.get("entity_ids", [])]
        if not whole_home and not entity_ids:
            raise HomeAssistantError("Choose at least one Assist satellite or Whole home")
        result = await manager.async_send(
            message,
            whole_home=whole_home,
            entity_ids=entity_ids,
            source="frontend",
        )
        connection.send_result(msg["id"], result)
    except (HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))


async def async_setup_intercom_panel(hass: HomeAssistant) -> None:
    """Register the typed intercom panel once."""
    if hass.data.get(_SETUP_KEY):
        return
    hass.data[_SETUP_KEY] = True
    frontend_path = Path(__file__).parent / "frontend" / "intercom-panel.js"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"/{DOMAIN}/intercom-panel.js",
                str(frontend_path),
                cache_headers=False,
            )
        ]
    )
    websocket_api.async_register_command(hass, websocket_intercom)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="extended-openai-intercom-panel",
        frontend_url_path=PANEL_URL,
        module_url=f"/{DOMAIN}/intercom-panel.js",
        sidebar_title=PANEL_TITLE,
        sidebar_icon="mdi:bullhorn-outline",
        require_admin=False,
    )
