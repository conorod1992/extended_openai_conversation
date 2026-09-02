"""Administrator API and frontend modules for opt-in full request debugging."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .debug import get_debug_manager

DEBUG_WS_COMMAND = f"{DOMAIN}/request_debug"
_DEBUG_UI_SETUP = f"{DOMAIN}.request_debug_ui_setup"


def _agents(hass: HomeAssistant) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        for subentry in entry.subentries.values():
            if subentry.subentry_type != "conversation":
                continue
            result.append(
                {
                    "entry_id": entry.entry_id,
                    "subentry_id": subentry.subentry_id,
                    "title": subentry.title,
                }
            )
    return sorted(result, key=lambda item: item["title"].casefold())


def _manager(hass: HomeAssistant, msg: dict[str, Any]):
    entry_id = msg.get("entry_id")
    subentry_id = msg.get("subentry_id")
    if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
        raise HomeAssistantError("entry_id and subentry_id are required")
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return get_debug_manager(hass, entry_id, subentry_id)


@websocket_api.websocket_command(
    {
        vol.Required("type"): DEBUG_WS_COMMAND,
        vol.Required("action"): str,
        vol.Optional("entry_id"): str,
        vol.Optional("subentry_id"): str,
        vol.Optional("debug_id"): str,
        vol.Optional("enabled"): bool,
        vol.Optional("limit"): int,
        vol.Optional("confirm"): bool,
    }
)
@websocket_api.async_response
async def websocket_request_debug(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Serve bounded request-debug data to administrators only."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator required")
        return
    try:
        action = msg["action"]
        if action == "agents":
            result: Any = {"agents": _agents(hass)}
        else:
            manager = _manager(hass, msg)
            if action == "status":
                result = manager.status()
            elif action == "configure":
                manager.configure(
                    enabled=msg.get("enabled"),
                    limit=msg.get("limit"),
                )
                result = manager.status()
            elif action == "runs":
                result = {"runs": manager.summaries(), **manager.status()}
            elif action == "get":
                debug_id = msg.get("debug_id")
                if not isinstance(debug_id, str):
                    raise HomeAssistantError("debug_id is required")
                trace = manager.get(debug_id)
                if trace is None:
                    raise HomeAssistantError("Debug run not found")
                result = {
                    "trace": trace,
                    "copy_text": json.dumps(trace, indent=2, ensure_ascii=False),
                }
            elif action == "clear":
                if msg.get("confirm") is not True:
                    raise HomeAssistantError("Explicit confirmation is required")
                result = {"deleted": manager.clear(), **manager.status()}
            else:
                raise HomeAssistantError(f"Unknown debug action: {action}")
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return
    connection.send_result(msg["id"], result)


async def async_setup_debug_ui(hass: HomeAssistant) -> None:
    """Register request-debug API/modules used by Usage & Maintenance."""
    if hass.data.get(_DEBUG_UI_SETUP):
        return
    hass.data[_DEBUG_UI_SETUP] = True
    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"/{DOMAIN}/{module_name}",
                str(frontend_dir / module_name),
                cache_headers=False,
            )
            for module_name in ("debug-panel.js", "debug-management.js")
        ]
    )
    websocket_api.async_register_command(hass, websocket_request_debug)
