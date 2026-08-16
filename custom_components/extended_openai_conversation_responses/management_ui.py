"""Unified authenticated management API and single Home Assistant panel."""

from __future__ import annotations

import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .agent_config import (
    AGENT_CONFIG_FIELDS,
    AgentConfigError,
    agent_config_defaults,
    agent_config_options,
    agent_config_snapshot,
    function_tool_yaml,
    merge_agent_config,
    model_capabilities,
    normalize_agent_config,
    starter_function_tool_yaml,
    validate_function_tools,
    validate_single_function_tool,
)
from .agent_test import async_test_agent
from .backup import async_create_backup, async_restore_backup, inspect_backup
from .built_in_functions import built_in_function_catalog
from .const import (
    AGENT_CONFIG_EXPORT_VERSION,
    CONF_API_PROVIDER,
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_SKILLS,
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
    DEFAULT_CONVERSATION_CONTINUITY,
    DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    DOMAIN,
    MANAGEMENT_PANEL_TITLE,
    MANAGEMENT_PANEL_URL,
)
from .continuity import ConversationContinuity, async_get_continuity
from .conversation_archive import async_get_archive
from .function_groups import get_function_group_runtime
from .functions import FUNCTIONS
from .helpers import get_exposed_entities
from .knowledge import (
    async_get_knowledge,
    get_loaded_knowledge,
    knowledge_source_as_dict,
)
from .memory import ANONYMOUS_USER_ID, async_get_memory, get_memory_mode, memory_as_dict
from .prompt import render_effective_prompt
from .scope import SHARED_HOUSEHOLD_SCOPE_ID, user_scope
from .skills import SkillManager
from .speech import process_speech_text
from .temporary_memory import (
    async_get_temporary_memory,
    get_loaded_temporary_memory,
    temporary_memory_as_dict,
)
from .usage import async_get_usage

WS_COMMAND = f"{DOMAIN}/management"
_UI_SETUP = f"{DOMAIN}.management_ui_setup"
MANAGEMENT_FRONTEND_MODULES = (
    "management-panel.js",
    "agent-config-editor.js",
    "agent-config-help.js",
    "usage-chart.js",
)


def entry_and_agent(hass: HomeAssistant, entry_id: str, subentry_id: str):
    """Resolve an exact entry and conversation subentry for every management API."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry, subentry


async def _async_preview_effective_prompt(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    options: dict[str, Any],
    user_id: str,
) -> dict[str, Any]:
    """Render a side-effect-free fresh-request system/context baseline."""
    temporary_memories = []
    notes = [
        "User input and conversation history are excluded.",
        "Query-derived persistent memories are excluded because there is no user query.",
    ]
    temporary = get_loaded_temporary_memory(hass, entry.entry_id, subentry.subentry_id)
    scope = user_scope(user_id, source="management_preview")
    temporary_scope, _label = ConversationContinuity.identity_key(
        options.get(CONF_CONVERSATION_CONTINUITY, DEFAULT_CONVERSATION_CONTINUITY),
        scope,
        None,
    )
    if temporary is not None and temporary_scope is not None:
        temporary_memories = await temporary.async_active_snapshot(temporary_scope)
    elif temporary is not None:
        notes.append(
            "Active temporary memories are excluded because a fresh device or "
            "conversation scope cannot be identified without a request."
        )

    skill_manager = SkillManager.get_loaded_instance()
    enabled_names = set(options.get(CONF_SKILLS, []) or [])
    skills = (
        [
            skill
            for skill in skill_manager.get_all_skills()
            if skill.name in enabled_names
        ]
        if skill_manager is not None
        else []
    )
    knowledge = get_loaded_knowledge(hass, entry.entry_id, subentry.subentry_id)
    try:
        preview = render_effective_prompt(
            hass,
            options,
            exposed_entities=get_exposed_entities(hass),
            current_device_id=None,
            user_input=None,
            skills=skills,
            memories=None,
            temporary_memories=temporary_memories,
            knowledge_available=bool(
                options.get(CONF_KNOWLEDGE_ENABLED)
                and knowledge is not None
                and knowledge.source_count > 0
            ),
        )
    except Exception as err:
        concise = " ".join(str(err).split())[:500] or type(err).__name__
        raise HomeAssistantError(
            f"The effective prompt could not be rendered: {concise}"
        ) from err

    if (
        int(
            options.get(
                CONF_MEMORY_AUTO_RETRIEVE_LIMIT, DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT
            )
        )
        <= 0
    ):
        notes = [
            note
            for note in notes
            if not note.startswith("Query-derived persistent memories")
        ]
    return {
        "prompt": preview.text,
        "sections": [
            {
                "key": section.key,
                "label": section.label,
                "volatility": section.volatility,
            }
            for section in preview.sections
        ],
        "notes": notes,
    }


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


def _validation_result(callback) -> dict[str, Any]:
    """Run configuration validation and return frontend-friendly errors."""
    try:
        value = callback()
    except AgentConfigError as err:
        return {"valid": False, "errors": {err.field: str(err).split(": ", 1)[-1]}}
    return {"valid": True, "errors": {}, "config": value}


_SECRET_KEY = re.compile(
    r"(?:^|[_-])(?:api_?key|password|passwd|secret|token|authorization)(?:$|[_-])"
    r"|(?:apiKey|clientSecret|accessToken|refreshToken)$",
    re.IGNORECASE,
)


def _redact_export_secrets(value: Any, *, schema: bool = False) -> Any:
    """Remove likely credential values while preserving JSON-schema properties."""
    if isinstance(value, list):
        return [_redact_export_secrets(item, schema=schema) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        child_schema = schema or key in {"parameters", "properties", "items"}
        if not schema and _SECRET_KEY.search(str(key)):
            continue
        result[key] = _redact_export_secrets(item, schema=child_schema)
    return result


def _export_agent(subentry) -> dict[str, Any]:
    """Build a versioned configuration document with best-effort redaction."""
    return {
        "schema": "extended_openai_conversation.agent",
        "version": AGENT_CONFIG_EXPORT_VERSION,
        "title": subentry.title,
        "config": _redact_export_secrets(agent_config_snapshot(subentry.data)),
    }


def _parse_import_document(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError as err:
            raise AgentConfigError("document", f"invalid JSON/YAML: {err}") from err
    if not isinstance(value, dict):
        raise AgentConfigError("document", "must be an object")
    if value.get("schema") != "extended_openai_conversation.agent":
        raise AgentConfigError("schema", "unsupported or missing export schema")
    if value.get("version") != AGENT_CONFIG_EXPORT_VERSION:
        raise AgentConfigError("version", "unsupported export version")
    unknown = set(value) - {"schema", "version", "title", "config"}
    if unknown:
        raise AgentConfigError(
            "document", "unknown fields: " + ", ".join(sorted(unknown))
        )
    config = value.get("config")
    if not isinstance(config, dict):
        raise AgentConfigError("config", "must be an object")
    return {
        "title": str(value.get("title") or "Imported conversation agent").strip(),
        "config": normalize_agent_config(config),
    }


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
        return [
            scope_item(
                f"user:{user_id}", "user", (user.name or user_id) if user else user_id
            )
        ]
    users = await hass.auth.async_get_users()
    scopes = [
        scope_item(f"user:{user.id}", "user", user.name or user.id) for user in users
    ]
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

    if section == "backup":
        _require_admin(is_admin)
        if action == "create":
            return await async_create_backup(hass, entry, subentry)
        if action == "inspect":
            prepared = inspect_backup(message.get("document"), subentry.subentry_id)
            return {
                "valid": True,
                "title": prepared.title,
                "summary": prepared.summary(),
            }
        if action == "restore":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            return await async_restore_backup(
                hass, entry, subentry, message.get("document")
            )

    if section == "configuration":
        _require_admin(is_admin)
        if action == "get":
            config = agent_config_snapshot(subentry.data)
            return {
                "title": subentry.title,
                "config": config,
                "defaults": agent_config_snapshot(agent_config_defaults()),
                "options": agent_config_options(),
                "model_capabilities": model_capabilities(config[CONF_CHAT_MODEL]),
                "function_types": sorted(FUNCTIONS),
            }
        if action == "validate":
            updates = message.get("config", {})
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            result = _validation_result(
                lambda: agent_config_snapshot(
                    merge_agent_config(subentry.data, updates)
                )
            )
            if result["valid"]:
                result["model_capabilities"] = model_capabilities(
                    result["config"][CONF_CHAT_MODEL]
                )
            return result
        if action == "update":
            _require_admin(is_admin)
            updates = message.get("config")
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            normalized = merge_agent_config(subentry.data, updates)
            title = message.get("title")
            if title is not None and (not isinstance(title, str) or not title.strip()):
                raise AgentConfigError("title", "must not be empty")
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data=normalized,
                **({"title": title.strip()} if isinstance(title, str) else {}),
            )
            snapshot = agent_config_snapshot(normalized)
            return {
                "title": title.strip() if isinstance(title, str) else subentry.title,
                "config": snapshot,
                "model_capabilities": model_capabilities(snapshot[CONF_CHAT_MODEL]),
            }
        if action == "duplicate":
            _require_admin(is_admin)
            requested_title = message.get("title")
            title = (
                requested_title.strip()
                if isinstance(requested_title, str) and requested_title.strip()
                else f"{subentry.title} - Copy"
            )
            duplicate = ConfigSubentry(
                data=MappingProxyType(
                    normalize_agent_config(
                        {
                            key: value
                            for key, value in subentry.data.items()
                            if key in AGENT_CONFIG_FIELDS
                        }
                    )
                ),
                subentry_type="conversation",
                title=title,
                unique_id=None,
            )
            hass.config_entries.async_add_subentry(entry, duplicate)
            return {
                "status": "created",
                "entry_id": entry.entry_id,
                "subentry_id": duplicate.subentry_id,
                "title": duplicate.title,
            }
        if action == "export":
            document = _export_agent(subentry)
            return {
                "document": document,
                "json": json.dumps(document, indent=2, ensure_ascii=False),
            }
        if action == "import_preview":
            parsed = _parse_import_document(message.get("document"))
            return {
                "valid": True,
                "title": parsed["title"],
                "config": agent_config_snapshot(parsed["config"]),
                "summary": {
                    "model": parsed["config"][CONF_CHAT_MODEL],
                    "tools": len(
                        validate_function_tools(parsed["config"].get("functions"))
                    ),
                    "function_groups": len(parsed["config"].get("function_groups", [])),
                    "speech_rules": len(
                        parsed["config"].get("speech_regex_replacements", [])
                    ),
                },
            }
        if action == "import":
            _require_admin(is_admin)
            parsed = _parse_import_document(message.get("document"))
            mode = message.get("mode", "current")
            if mode == "current":
                if message.get("confirm") is not True:
                    raise HomeAssistantError("Explicit confirmation is required")
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=parsed["config"], title=parsed["title"]
                )
                return {"status": "updated", "subentry_id": subentry.subentry_id}
            if mode != "new":
                raise HomeAssistantError("mode must be current or new")
            imported = ConfigSubentry(
                data=MappingProxyType(parsed["config"]),
                subentry_type="conversation",
                title=parsed["title"],
                unique_id=None,
            )
            hass.config_entries.async_add_subentry(entry, imported)
            return {"status": "created", "subentry_id": imported.subentry_id}
        if action == "speech_preview":
            sample = message.get("sample_text", "")
            updates = message.get("config", {})
            if not isinstance(sample, str) or not isinstance(updates, dict):
                raise HomeAssistantError("sample_text and config are invalid")
            normalized = merge_agent_config(subentry.data, updates)
            return {"speech_text": process_speech_text(sample, normalized)}
        if action == "prompt_preview":
            updates = message.get("config", {})
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            normalized = merge_agent_config(subentry.data, updates)
            return await _async_preview_effective_prompt(
                hass, entry, subentry, normalized, user_id
            )

    if section == "tools":
        _require_admin(is_admin)
        if action == "validate":
            return _validation_result(
                lambda: validate_function_tools(message.get("tools"))
            )
        if action == "serialize":
            return {"yaml": function_tool_yaml(message.get("tool"))}
        if action == "starter":
            return {"yaml": starter_function_tool_yaml()}
        if action == "built_in_catalog":
            configured = validate_function_tools(message.get("tools", []))
            return {
                "functions": [
                    {
                        "label": preset["label"],
                        "implementation": preset["implementation"],
                        "name": preset["tool"]["spec"]["name"],
                        "description": preset["tool"]["spec"]["description"],
                        "already_configured": preset["already_configured"],
                        "yaml": function_tool_yaml(preset["tool"]),
                    }
                    for preset in built_in_function_catalog(configured)
                ]
            }
        if action == "validate_yaml":
            result = _validation_result(
                lambda: validate_single_function_tool(message.get("yaml"))
            )
            if result["valid"]:
                tool = result["config"]
                result.update(
                    {
                        "yaml": function_tool_yaml(tool),
                        "name": tool["spec"]["name"],
                        "type": tool["function"]["type"],
                        "description": tool["spec"].get("description", ""),
                    }
                )
            return result

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
        continuity = async_get_continuity(hass, entry_id, subentry_id)
        if action == "active":
            _require_admin(is_admin)
            return {
                "active": await continuity.async_list(
                    int(
                        subentry.data.get(
                            CONF_CONVERSATION_TIMEOUT_MINUTES,
                            DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
                        )
                    )
                )
            }
        if action == "end_active":
            _require_admin(is_admin)
            key = message.get("continuity_key")
            if not isinstance(key, str):
                raise HomeAssistantError("continuity_key is required")
            ended = await continuity.async_end(key)
            if ended:
                function_groups = get_function_group_runtime(
                    hass, entry_id, subentry_id
                )
                if function_groups is not None:
                    function_groups.end(f"continuity:{key}")
            return {"ended": int(ended)}
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
        if action.startswith("temporary_"):
            temporary = await async_get_temporary_memory(hass, entry_id, subentry_id)
            if action == "temporary_list":
                temporary_records = (
                    await temporary.async_list_all()
                    if is_admin
                    else await temporary.async_list(scope_id)
                )
                return {
                    "memories": [
                        temporary_memory_as_dict(record, include_scope=is_admin)
                        for record in temporary_records
                    ]
                }
            if action == "temporary_delete":
                memory_id = str(message.get("memory_id", ""))
                requested_scope = message.get("temporary_scope_id", scope_id)
                if not isinstance(requested_scope, str):
                    raise HomeAssistantError("temporary_scope_id is invalid")
                if not is_admin and requested_scope != scope_id:
                    raise HomeAssistantError("This temporary memory is not available")
                return {
                    "deleted": await temporary.async_delete(
                        requested_scope, [memory_id]
                    )
                }
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
    """Compatibility wrapper using the shared agent configuration contract."""
    try:
        normalized = merge_agent_config({}, settings)
    except AgentConfigError as err:
        if err.field == "config":
            raise HomeAssistantError(
                str(err).replace("config: unknown fields", "Unknown settings")
            ) from err
        raise
    return {key: normalized[key] for key in settings}


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
        vol.Optional("temporary_scope_id"): str,
        vol.Optional("continuity_key"): str,
        vol.Optional("settings"): dict,
        vol.Optional("config"): dict,
        vol.Optional("tools"): vol.Any(str, list),
        vol.Optional("tool"): dict,
        vol.Optional("yaml"): str,
        vol.Optional("document"): vol.Any(str, dict),
        vol.Optional("sample_text"): str,
        vol.Optional("mode"): str,
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
    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                f"/{DOMAIN}/{module_name}",
                str(frontend_dir / module_name),
                cache_headers=False,
            )
            for module_name in MANAGEMENT_FRONTEND_MODULES
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
