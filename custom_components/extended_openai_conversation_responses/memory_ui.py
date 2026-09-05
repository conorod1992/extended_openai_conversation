"""Home Assistant panel and WebSocket backend for memories and agent tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .agent_test import async_test_agent
from .const import (
    CONF_SHARED_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_TEMPORARY_MEMORY,
    DOMAIN,
    MEMORY_PANEL_TITLE,
    MEMORY_PANEL_URL,
    SHARED_MEMORY_DISABLED,
    TEMPORARY_MEMORY_OFF,
)
from .memory import async_get_memory, get_memory_mode, memory_as_dict
from .scope import SHARED_HOUSEHOLD_SCOPE_ID
from .temporary_memory import (
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    TemporaryMemoryRecord,
    async_get_temporary_memory,
    temporary_memory_as_dict,
)

WS_COMMAND = f"{DOMAIN}/manage"
_UI_SETUP = f"{DOMAIN}.memory_ui_setup"


def _entry_and_agent(hass: HomeAssistant, entry_id: str, subentry_id: str):
    """Resolve an exact integration entry and conversation-agent subentry."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry, subentry


async def _async_user_temporary_records(
    temporary_memory: TemporaryMemory, scope_id: str
) -> list[TemporaryMemoryRecord]:
    """Return only active Temporary Memory owned by one exact user scope."""
    return [
        record
        for record in await temporary_memory.async_list_all()
        if record.scope_id == scope_id
    ]


async def async_manage_command(
    hass: HomeAssistant, user_id: str, message: dict[str, Any]
) -> dict[str, Any]:
    """Execute one user-scoped UI operation."""
    action = message["action"]
    if action == "agents":
        return {
            "agents": [
                {
                    "entry_id": entry.entry_id,
                    "entry_title": entry.title,
                    "subentry_id": subentry.subentry_id,
                    "title": subentry.title,
                    "memory_mode": get_memory_mode(subentry.data),
                    "shared_memory_enabled": subentry.data.get(
                        CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE
                    )
                    != SHARED_MEMORY_DISABLED,
                    "temporary_memory_enabled": subentry.data.get(
                        CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY
                    )
                    != TEMPORARY_MEMORY_OFF,
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
    entry, subentry = _entry_and_agent(hass, entry_id, subentry_id)

    if action == "test_agent":
        return (await async_test_agent(hass, entry, subentry)).as_dict()

    temporary_enabled = (
        subentry.data.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
        != TEMPORARY_MEMORY_OFF
    )
    temporary_scope_id = f"user:{user_id}"
    if action in {"temporary_delete", "temporary_clear"}:
        if not temporary_enabled:
            raise HomeAssistantError("Temporary Memory is disabled for this agent")
        temporary_memory = await async_get_temporary_memory(
            hass, entry_id, subentry_id
        )
        if action == "temporary_delete":
            memory_id = message.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id:
                raise HomeAssistantError("memory_id is required")
            return {
                "deleted": await temporary_memory.async_delete(
                    temporary_scope_id, [memory_id]
                )
            }
        if message.get("confirm") is not True:
            raise HomeAssistantError("Explicit confirmation is required")
        records = await _async_user_temporary_records(
            temporary_memory, temporary_scope_id
        )
        memory_ids = [record.memory_id for record in records]
        deleted = 0
        for start in range(0, len(memory_ids), MAX_DELETE_RECORDS):
            deleted += await temporary_memory.async_delete(
                temporary_scope_id,
                memory_ids[start : start + MAX_DELETE_RECORDS],
            )
        return {"deleted": deleted}

    memory = await async_get_memory(hass, entry_id, subentry_id)
    shared_enabled = (
        subentry.data.get(CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE)
        != SHARED_MEMORY_DISABLED
    )
    readable_scopes = [
        user_id,
        *([SHARED_HOUSEHOLD_SCOPE_ID] if shared_enabled else []),
    ]
    requested_scope = message.get("scope")
    if requested_scope not in {None, "personal", "household"}:
        raise HomeAssistantError("scope must be personal or household")
    if requested_scope == "household":
        if not shared_enabled:
            raise HomeAssistantError("Shared household memory is disabled")
        write_scope = SHARED_HOUSEHOLD_SCOPE_ID
    else:
        write_scope = user_id
    if action == "list":
        records = await memory.async_list(
            readable_scopes[0] if len(readable_scopes) == 1 else readable_scopes,
            message.get("category"),
            int(message.get("limit", 100)),
            int(message.get("offset", 0)),
        )
        temporary_records: list[TemporaryMemoryRecord] = []
        if temporary_enabled:
            temporary_memory = await async_get_temporary_memory(
                hass, entry_id, subentry_id
            )
            temporary_records = await _async_user_temporary_records(
                temporary_memory, temporary_scope_id
            )
        return {
            "memories": [
                memory_as_dict(record, include_scope=True, personal_scope_id=user_id)
                for record in records
            ],
            "temporary_memories": [
                temporary_memory_as_dict(record) for record in temporary_records
            ],
        }
    if action == "add":
        add_args = (
            write_scope,
            str(message.get("content", "")),
            str(message.get("category", "general")),
            "explicit",
        )
        if any(
            key in message for key in ("importance", "subject", "key", "valid_from")
        ):
            result = await memory.async_add(
                *add_args,
                str(message.get("importance", "normal")),
                message.get("subject"),
                message.get("key"),
                message.get("valid_from"),
            )
        else:
            result = await memory.async_add(*add_args)
        return result
    if action == "update":
        original_scope = message.get("original_scope")
        if original_scope not in {None, "personal", "household"}:
            raise HomeAssistantError("original_scope must be personal or household")
        original_scope_id = (
            SHARED_HOUSEHOLD_SCOPE_ID if original_scope == "household" else user_id
        )
        memory_id = str(message.get("memory_id", ""))
        update_args = (
            original_scope_id,
            memory_id,
            message.get("content"),
            message.get("category"),
        )
        if any(
            key in message for key in ("importance", "subject", "key", "valid_from")
        ):
            record = await memory.async_update(
                *update_args,
                message.get("importance"),
                message.get("subject"),
                message.get("key"),
                message.get("valid_from"),
                target_user_id=write_scope,
            )
        elif original_scope is not None:
            record = await memory.async_update(*update_args, target_user_id=write_scope)
        else:
            record = await memory.async_update(*update_args)
        return {
            "status": "updated",
            "memory": memory_as_dict(
                record, include_scope=True, personal_scope_id=user_id
            ),
        }
    if action == "delete":
        deleted = await memory.async_delete(
            write_scope, [str(message.get("memory_id", ""))]
        )
        return {"deleted": deleted}
    if action == "clear":
        if message.get("confirm") is not True:
            raise HomeAssistantError("Explicit confirmation is required")
        category = message.get("category")
        if category is not None and not isinstance(category, str):
            raise HomeAssistantError("category must be a string")
        return {"deleted": await memory.async_clear(write_scope, category)}
    raise HomeAssistantError(f"Unknown management action: {action}")


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_COMMAND,
        vol.Required("action"): str,
        vol.Optional("entry_id"): str,
        vol.Optional("subentry_id"): str,
        vol.Optional("memory_id"): str,
        vol.Optional("content"): str,
        vol.Optional("category"): str,
        vol.Optional("importance"): str,
        vol.Optional("scope"): str,
        vol.Optional("original_scope"): str,
        vol.Optional("subject"): str,
        vol.Optional("key"): str,
        vol.Optional("valid_from"): str,
        vol.Optional("limit"): int,
        vol.Optional("offset"): int,
        vol.Optional("confirm"): bool,
    }
)
@websocket_api.async_response
async def websocket_manage(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Handle the authenticated management command."""
    try:
        result = await async_manage_command(hass, connection.user.id, msg)
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return
    connection.send_result(msg["id"], result)


async def async_setup_memory_ui(hass: HomeAssistant) -> None:
    """Register the integration-owned management panel once."""
    if hass.data.get(_UI_SETUP):
        return
    hass.data[_UI_SETUP] = True
    panel_file = Path(__file__).parent / "frontend" / "memory-panel.js"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"/{DOMAIN}/memory-panel.js",
                str(panel_file),
                cache_headers=False,
            )
        ]
    )
    websocket_api.async_register_command(hass, websocket_manage)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="extended-openai-memory-panel",
        frontend_url_path=MEMORY_PANEL_URL,
        module_url=f"/{DOMAIN}/memory-panel.js",
        sidebar_title=MEMORY_PANEL_TITLE,
        sidebar_icon="mdi:brain",
        require_admin=False,
    )