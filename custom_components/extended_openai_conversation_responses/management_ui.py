"""Unified authenticated management API and single Home Assistant panel."""

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
    ARCHIVE_RETENTION_OPTIONS,
    CONF_API_PROVIDER,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_CHAT_MODEL,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    DEFAULT_API_PROVIDER,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_RETENTION_DAYS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    DOMAIN,
    MANAGEMENT_PANEL_TITLE,
    MANAGEMENT_PANEL_URL,
    SHARED_MEMORY_MODES,
    USAGE_RETENTION_OPTIONS,
    VOICE_POLICIES,
)
from .conversation_archive import async_get_archive
from .knowledge import async_get_knowledge, knowledge_source_as_dict
from .memory import ANONYMOUS_USER_ID, async_get_memory, get_memory_mode, memory_as_dict
from .scope import SHARED_HOUSEHOLD_SCOPE_ID
from .usage import async_get_usage

WS_COMMAND = f"{DOMAIN}/management"
_UI_SETUP = f"{DOMAIN}.management_ui_setup"


def entry_and_agent(hass: HomeAssistant, entry_id: str, subentry_id: str):
    """Resolve an exact entry and conversation subentry for every management API."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry, subentry


def _require_admin(is_admin: bool) -> None:
    if not is_admin:
        raise HomeAssistantError("Administrator permission is required")


def _selected_scope(user_id: str, is_admin: bool, requested: Any) -> str:
    personal = f"user:{user_id}"
    if requested is None:
        return personal
    if not isinstance(requested, str):
        raise HomeAssistantError("scope_id must be a string")
    if not is_admin and requested != personal:
        raise HomeAssistantError("This scope is not available to the current user")
    if requested in {
        SHARED_HOUSEHOLD_SCOPE_ID,
        ANONYMOUS_USER_ID,
    } or requested.startswith("user:"):
        return requested
    raise HomeAssistantError("Unknown data scope")


def _memory_scope(scope_id: str) -> str:
    return scope_id.removeprefix("user:") if scope_id.startswith("user:") else scope_id


async def _scope_catalog(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    memory_counts: dict[str, int] | None = None,
    conversation_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    memory_counts = memory_counts or {}
    conversation_counts = conversation_counts or {}

    def scope_item(scope_id: str, scope_type: str, display_name: str) -> dict[str, Any]:
        owner = _memory_scope(scope_id)
        return {
            "scope_id": scope_id,
            "scope_type": scope_type,
            "display_name": display_name,
            "is_current_user": scope_id == f"user:{user_id}",
            "memory_count": memory_counts.get(owner, 0),
            "conversation_count": conversation_counts.get(scope_id, 0),
        }

    if not is_admin:
        user = await hass.auth.async_get_user(user_id)
        return [scope_item(f"user:{user_id}", "user", user.name if user else user_id)]
    users = await hass.auth.async_get_users()
    scopes = [scope_item(f"user:{user.id}", "user", user.name) for user in users]
    scopes.append(scope_item(SHARED_HOUSEHOLD_SCOPE_ID, "shared", "Shared household"))
    legacy = scope_item(ANONYMOUS_USER_ID, "anonymous_legacy", "Legacy anonymous")
    if legacy["memory_count"] or legacy["conversation_count"]:
        scopes.append(legacy)
    return scopes


async def async_management_command(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Execute one narrow, validated management operation."""
    section = message.get("section", "overview")
    action = message["action"]
    if action == "agents":
        agents = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            for subentry in entry.subentries.values():
                if subentry.subentry_type != "conversation":
                    continue
                usage = await async_get_usage(
                    hass, entry.entry_id, subentry.subentry_id
                )
                memory = await async_get_memory(
                    hass, entry.entry_id, subentry.subentry_id
                )
                knowledge = await async_get_knowledge(
                    hass, entry.entry_id, subentry.subentry_id
                )
                agents.append(
                    {
                        "entry_id": entry.entry_id,
                        "entry_title": entry.title,
                        "subentry_id": subentry.subentry_id,
                        "title": subentry.title,
                        "provider": entry.data.get(
                            CONF_API_PROVIDER, DEFAULT_API_PROVIDER
                        ),
                        "model": subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
                        "memory_mode": get_memory_mode(subentry.data),
                        "memory_count": memory.stats()["memory_count"],
                        "knowledge_enabled": bool(
                            subentry.data.get("knowledge_enabled", False)
                        ),
                        "knowledge_source_count": knowledge.source_count,
                        "archive_enabled": bool(
                            subentry.data.get(
                                CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED
                            )
                        ),
                        "tokens_today": usage.today_summary()["total_tokens"],
                    }
                )
        return {
            "agents": agents,
            "scopes": await _scope_catalog(hass, user_id, is_admin),
            "is_admin": is_admin,
        }

    entry_id = message.get("entry_id")
    subentry_id = message.get("subentry_id")
    if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
        raise HomeAssistantError("entry_id and subentry_id are required")
    entry, subentry = entry_and_agent(hass, entry_id, subentry_id)

    if section == "scopes" and action == "catalog":
        memory = await async_get_memory(hass, entry_id, subentry_id)
        archive = await async_get_archive(hass, entry_id, subentry_id)
        return {
            "scopes": await _scope_catalog(
                hass,
                user_id,
                is_admin,
                memory.scope_counts(),
                archive.scope_counts(),
            )
        }

    if section == "diagnostics" and action == "test_agent":
        return (await async_test_agent(hass, entry, subentry)).as_dict()

    if section == "usage":
        usage = await async_get_usage(hass, entry_id, subentry_id)
        if action == "summary":
            return {
                "lifetime": usage.as_dict(),
                "today": usage.today_summary(),
                "month": usage.month_summary(),
                "latest": asdict_or_none(usage.latest_run),
            }
        if action == "daily":
            return {
                "days": usage.daily_series(
                    str(message.get("start_date", "0000-01-01")),
                    str(message.get("end_date", "9999-12-31")),
                )
            }
        if action == "runs":
            return usage.recent_runs(
                limit=int(message.get("limit", 50)),
                offset=int(message.get("offset", 0)),
                successful=message.get("successful"),
            )
        if action == "requests":
            run_id = message.get("run_id")
            if not isinstance(run_id, str):
                raise HomeAssistantError("run_id is required")
            return usage.requests_for_run(
                run_id,
                limit=int(message.get("limit", 100)),
                offset=int(message.get("offset", 0)),
            )
        if action == "breakdowns":
            return usage.breakdowns(message.get("start_date"), message.get("end_date"))
        if action == "retention":
            return {
                "request_days": usage.request_retention_days,
                "run_days": usage.run_retention_days,
            }
        if action == "clear_details":
            _require_admin(is_admin)
            return await usage.async_clear_details(
                confirm=message.get("confirm") is True
            )

    scope_id = _selected_scope(user_id, is_admin, message.get("scope_id"))
    if section == "conversations":
        archive = await async_get_archive(hass, entry_id, subentry_id)
        if action == "list":
            return await archive.async_list_sessions(
                scope_id,
                limit=int(message.get("limit", 50)),
                offset=int(message.get("offset", 0)),
            )
        if action == "search":
            return await archive.async_search(
                scope_id,
                str(message.get("query", "")),
                start_date=message.get("start_date"),
                end_date=message.get("end_date"),
                limit=int(message.get("limit", 20)),
                offset=int(message.get("offset", 0)),
            )
        if action == "get":
            return await archive.async_get(
                scope_id,
                str(message.get("session_id", "")),
                int(message.get("start_turn", 0)),
                int(message.get("limit", 20)),
            )
        if action == "delete":
            return await archive.async_delete_session(
                scope_id, str(message.get("session_id", ""))
            )
        if action == "clear":
            return await archive.async_clear_scope(
                scope_id, confirm=message.get("confirm") is True
            )
        if action == "delete_range":
            return await archive.async_delete_date_range(
                scope_id,
                str(message.get("start_date", "")),
                str(message.get("end_date", "")),
                confirm=message.get("confirm") is True,
            )
        if action == "settings":
            return _settings_snapshot(subentry.data)

    if section == "memories":
        memory = await async_get_memory(hass, entry_id, subentry_id)
        owner = _memory_scope(scope_id)
        if action == "list":
            records = await memory.async_list(
                owner,
                message.get("category"),
                int(message.get("limit", 100)),
                int(message.get("offset", 0)),
            )
            return {
                "memories": [
                    memory_as_dict(record, include_scope=is_admin) for record in records
                ],
                "scope_id": scope_id,
            }
        if action == "add":
            return await memory.async_add(
                owner,
                str(message.get("content", "")),
                str(message.get("category", "general")),
                "explicit",
            )
        if action == "update":
            record = await memory.async_update(
                owner,
                str(message.get("memory_id", "")),
                message.get("content"),
                message.get("category"),
            )
            return {
                "status": "updated",
                "memory": memory_as_dict(record, include_scope=is_admin),
            }
        if action == "delete":
            return {
                "deleted": await memory.async_delete(
                    owner, [str(message.get("memory_id", ""))]
                )
            }
        if action == "clear":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            return {"deleted": await memory.async_clear(owner, message.get("category"))}
        if action == "reassign_legacy":
            _require_admin(is_admin)
            target = _selected_scope(user_id, True, message.get("target_scope_id"))
            memory_ids = message.get("memory_ids")
            if not isinstance(memory_ids, list) or not all(
                isinstance(value, str) for value in memory_ids
            ):
                raise HomeAssistantError("memory_ids must be a list of strings")
            return await memory.async_reassign(
                ANONYMOUS_USER_ID, _memory_scope(target), memory_ids
            )

    if section == "knowledge":
        library = await async_get_knowledge(hass, entry_id, subentry_id)
        if action == "list":
            return {"sources": await library.async_list(), "stats": library.stats()}
        if action == "get":
            return {
                "source": knowledge_source_as_dict(
                    await library.async_get(str(message.get("source_id", "")))
                )
            }
        if action == "create":
            source = await library.async_create(
                message.get("title", ""),
                message.get("description", ""),
                message.get("content", ""),
            )
            return {"status": "created", "source": knowledge_source_as_dict(source)}
        if action == "update":
            source = await library.async_update(
                str(message.get("source_id", "")),
                message.get("title"),
                message.get("description"),
                message.get("content"),
            )
            return {"status": "updated", "source": knowledge_source_as_dict(source)}
        if action == "delete":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            return {
                "deleted": int(
                    await library.async_delete(str(message.get("source_id", "")))
                )
            }

    if section == "settings" and action == "update":
        _require_admin(is_admin)
        updates = message.get("settings")
        if not isinstance(updates, dict):
            raise HomeAssistantError("settings must be an object")
        normalized = _validate_settings(updates)
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**subentry.data, **normalized}
        )
        return {"settings": _settings_snapshot({**subentry.data, **normalized})}
    raise HomeAssistantError(f"Unknown {section} management action: {action}")


def _validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        CONF_ARCHIVE_ENABLED,
        CONF_ARCHIVE_RETENTION_DAYS,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
        CONF_SHARED_ARCHIVE_ENABLED,
        CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
        CONF_VOICE_SCOPE_POLICY,
        CONF_VOICE_DEFAULT_USER_ID,
        CONF_VOICE_DEVICE_MAPPINGS,
        CONF_VOICE_UNMAPPED_POLICY,
        CONF_SHARED_MEMORY_MODE,
        CONF_USAGE_REQUEST_RETENTION_DAYS,
        CONF_USAGE_RUN_RETENTION_DAYS,
    }
    unknown = set(settings) - allowed
    if unknown:
        raise HomeAssistantError("Unknown settings: " + ", ".join(sorted(unknown)))
    result = dict(settings)
    for key in (
        CONF_ARCHIVE_ENABLED,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
        CONF_SHARED_ARCHIVE_ENABLED,
    ):
        if key in result and not isinstance(result[key], bool):
            raise HomeAssistantError(f"{key} must be a boolean")
    if (
        CONF_ARCHIVE_RETENTION_DAYS in result
        and result[CONF_ARCHIVE_RETENTION_DAYS] not in ARCHIVE_RETENTION_OPTIONS
    ):
        raise HomeAssistantError("unsupported archive retention")
    for key in (CONF_USAGE_REQUEST_RETENTION_DAYS, CONF_USAGE_RUN_RETENTION_DAYS):
        if key in result and result[key] not in USAGE_RETENTION_OPTIONS:
            raise HomeAssistantError(f"unsupported {key}")
    for key in (CONF_VOICE_SCOPE_POLICY, CONF_VOICE_UNMAPPED_POLICY):
        if key in result and result[key] not in VOICE_POLICIES:
            raise HomeAssistantError(f"unsupported {key}")
    if (
        CONF_SHARED_MEMORY_MODE in result
        and result[CONF_SHARED_MEMORY_MODE] not in SHARED_MEMORY_MODES
    ):
        raise HomeAssistantError("unsupported shared memory mode")
    if CONF_VOICE_DEVICE_MAPPINGS in result:
        mappings = result[CONF_VOICE_DEVICE_MAPPINGS]
        if not isinstance(mappings, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in mappings.items()
        ):
            raise HomeAssistantError(
                "voice_device_mappings must map device IDs to scope owners"
            )
    if CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES in result:
        value = result[CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 1440
        ):
            raise HomeAssistantError(
                "archive_session_timeout_minutes must be 1 to 1440"
            )
    return result


def _settings_snapshot(options: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        CONF_ARCHIVE_ENABLED: DEFAULT_ARCHIVE_ENABLED,
        CONF_ARCHIVE_RETENTION_DAYS: DEFAULT_ARCHIVE_RETENTION_DAYS,
        CONF_ARCHIVE_MODEL_SEARCH_ENABLED: False,
        CONF_SHARED_ARCHIVE_ENABLED: False,
        CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: 30,
        CONF_VOICE_SCOPE_POLICY: DEFAULT_VOICE_SCOPE_POLICY,
        CONF_VOICE_DEFAULT_USER_ID: None,
        CONF_VOICE_DEVICE_MAPPINGS: {},
        CONF_VOICE_UNMAPPED_POLICY: DEFAULT_VOICE_UNMAPPED_POLICY,
        CONF_SHARED_MEMORY_MODE: DEFAULT_SHARED_MEMORY_MODE,
        CONF_USAGE_REQUEST_RETENTION_DAYS: DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
        CONF_USAGE_RUN_RETENTION_DAYS: DEFAULT_USAGE_RUN_RETENTION_DAYS,
    }
    return {key: options.get(key, default) for key, default in defaults.items()}


def asdict_or_none(value: Any) -> dict[str, Any] | None:
    from dataclasses import asdict

    return asdict(value) if value is not None else None


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_COMMAND,
        vol.Required("action"): str,
        vol.Optional("section"): str,
        vol.Optional("entry_id"): str,
        vol.Optional("subentry_id"): str,
        vol.Optional("scope_id"): str,
        vol.Optional("target_scope_id"): str,
        vol.Optional("settings"): dict,
        vol.Optional("memory_ids"): list,
        vol.Optional("memory_id"): str,
        vol.Optional("session_id"): str,
        vol.Optional("source_id"): str,
        vol.Optional("run_id"): str,
        vol.Optional("query"): str,
        vol.Optional("content"): str,
        vol.Optional("title"): str,
        vol.Optional("description"): str,
        vol.Optional("category"): str,
        vol.Optional("start_date"): str,
        vol.Optional("end_date"): str,
        vol.Optional("limit"): int,
        vol.Optional("offset"): int,
        vol.Optional("start_turn"): int,
        vol.Optional("successful"): bool,
        vol.Optional("confirm"): bool,
    }
)
@websocket_api.async_response
async def websocket_management(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        result = await async_management_command(
            hass, connection.user.id, connection.user.is_admin, msg
        )
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        connection.send_error(msg["id"], "invalid_request", str(err))
        return
    connection.send_result(msg["id"], result)


async def async_setup_management_ui(hass: HomeAssistant) -> None:
    """Register exactly one integration-owned sidebar panel."""
    if hass.data.get(_UI_SETUP):
        return
    hass.data[_UI_SETUP] = True
    panel_file = Path(__file__).parent / "frontend" / "management-panel.js"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"/{DOMAIN}/management-panel.js", str(panel_file), cache_headers=False
            )
        ]
    )
    websocket_api.async_register_command(hass, websocket_management)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="extended-openai-management-panel",
        frontend_url_path=MANAGEMENT_PANEL_URL,
        module_url=f"/{DOMAIN}/management-panel.js",
        sidebar_title=MANAGEMENT_PANEL_TITLE,
        sidebar_icon="mdi:robot-outline",
        require_admin=False,
    )
