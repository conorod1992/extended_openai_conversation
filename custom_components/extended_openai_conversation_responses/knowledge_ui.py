"""Authenticated Home Assistant management API for the Knowledge Library."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_KNOWLEDGE_ENABLED,
    DEFAULT_KNOWLEDGE_ENABLED,
    DOMAIN,
    KNOWLEDGE_PANEL_TITLE,
    KNOWLEDGE_PANEL_URL,
)
from .knowledge import async_get_knowledge, knowledge_source_as_dict

WS_COMMAND = f"{DOMAIN}/knowledge"
_UI_SETUP = f"{DOMAIN}.knowledge_ui_setup"


def _entry_and_agent(hass: HomeAssistant, entry_id: str, subentry_id: str):
    """Resolve an exact integration entry and conversation-agent subentry."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry, subentry


async def async_manage_knowledge_command(
    hass: HomeAssistant, message: dict[str, Any]
) -> dict[str, Any]:
    """Execute one authenticated Knowledge Library management operation."""
    action = message["action"]
    if action == "agents":
        return {
            "agents": [
                {
                    "entry_id": entry.entry_id,
                    "entry_title": entry.title,
                    "subentry_id": subentry.subentry_id,
                    "title": subentry.title,
                    "knowledge_enabled": subentry.data.get(
                        CONF_KNOWLEDGE_ENABLED, DEFAULT_KNOWLEDGE_ENABLED
                    ),
                }
                for entry in hass.config_entries.async_entries(DOMAIN)
                for subentry in entry.subentries.values()
                if subentry.subentry_type == "conversation"
            ]
        }

    entry_id = message.get("entry_id")
    subentry_id = message.get("subentry_id")
    if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
        raise HomeAssistantError("entry_id and subentry_id are required")
    _entry_and_agent(hass, entry_id, subentry_id)
    library = await async_get_knowledge(hass, entry_id, subentry_id)

    if action == "list":
        return {"sources": await library.async_list(), "stats": library.stats()}
    if action == "get":
        source_id = message.get("source_id")
        if not isinstance(source_id, str):
            raise HomeAssistantError("source_id is required")
        return {"source": knowledge_source_as_dict(await library.async_get(source_id))}
    if action == "create":
        source = await library.async_create(
            message.get("title", ""),
            message.get("description", ""),
            message.get("content", ""),
        )
        return {"status": "created", "source": knowledge_source_as_dict(source)}
    if action == "update":
        source_id = message.get("source_id")
        if not isinstance(source_id, str):
            raise HomeAssistantError("source_id is required")
        source = await library.async_update(
            source_id,
            message.get("title"),
            message.get("description"),
            message.get("content"),
        )
        return {"status": "updated", "source": knowledge_source_as_dict(source)}
    if action == "delete":
        if message.get("confirm") is not True:
            raise HomeAssistantError("Explicit confirmation is required")
        source_id = message.get("source_id")
        if not isinstance(source_id, str):
            raise HomeAssistantError("source_id is required")
        return {"deleted": 1 if await library.async_delete(source_id) else 0}
    raise HomeAssistantError(f"Unknown knowledge management action: {action}")


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_COMMAND,
        vol.Required("action"): str,
        vol.Optional("entry_id"): str,
        vol.Optional("subentry_id"): str,
        vol.Optional("source_id"): str,
        vol.Optional("title"): str,
        vol.Optional("description"): str,
        vol.Optional("content"): str,
        vol.Optional("confirm"): bool,
    }
)
@websocket_api.async_response
async def websocket_manage_knowledge(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle the authenticated Knowledge Library command."""
    try:
        result = await async_manage_knowledge_command(hass, msg)
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return
    connection.send_result(msg["id"], result)


async def async_setup_knowledge_ui(hass: HomeAssistant) -> None:
    """Register the Knowledge Library management panel once."""
    if hass.data.get(_UI_SETUP):
        return
    hass.data[_UI_SETUP] = True
    panel_file = Path(__file__).parent / "frontend" / "knowledge-panel.js"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"/{DOMAIN}/knowledge-panel.js",
                str(panel_file),
                cache_headers=False,
            )
        ]
    )
    websocket_api.async_register_command(hass, websocket_manage_knowledge)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="extended-openai-knowledge-panel",
        frontend_url_path=KNOWLEDGE_PANEL_URL,
        module_url=f"/{DOMAIN}/knowledge-panel.js",
        sidebar_title=KNOWLEDGE_PANEL_TITLE,
        sidebar_icon="mdi:bookshelf",
        require_admin=False,
    )
