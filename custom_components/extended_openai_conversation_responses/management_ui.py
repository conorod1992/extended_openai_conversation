"""Unified authenticated management API and single Home Assistant panel."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from uuid import uuid4

import voluptuous as vol
import yaml

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, service as service_helper

from .agent_config import (
    AGENT_CONFIG_FIELDS,
    GUEST_V2_FIELDS,
    MAX_AGENT_TITLE_LENGTH,
    AgentConfigError,
    agent_config_defaults,
    agent_config_options,
    agent_config_snapshot,
    configured_function_tools_from_data,
    function_tool_enabled,
    function_tool_yaml,
    merge_agent_config,
    model_capabilities,
    normalize_agent_config,
    preserve_legacy_guest_policy,
    starter_function_tool_yaml,
    validate_agent_title,
    validate_function_groups,
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
    CONF_CONTINUE_CONVERSATION,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_ALLOWED_FUNCTION_NAMES,
    CONF_GUEST_MODE_ENABLED,
    CONF_GUEST_POLICY_VERSION,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_SKILLS,
    CONF_TEMPORARY_MEMORY,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONF_VOICE_DEFAULT_USER_ID,
    CONF_VOICE_DEVICE_MAPPINGS,
    CONF_VOICE_SCOPE_POLICY,
    CONF_VOICE_UNMAPPED_POLICY,
    CONTINUE_CONVERSATION_CONDITIONAL,
    DEFAULT_API_PROVIDER,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_RETENTION_DAYS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONTINUE_CONVERSATION,
    DEFAULT_CONVERSATION_CONTINUITY,
    DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_TEMPORARY_MEMORY,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_VOICE_SCOPE_POLICY,
    DEFAULT_VOICE_UNMAPPED_POLICY,
    DOMAIN,
    FUNCTION_GROUP_LOADER_TOOL_NAME,
    GUEST_POLICY_VERSION,
    MANAGEMENT_PANEL_TITLE,
    MANAGEMENT_PANEL_URL,
    SERVICE_CALL_FUNCTION,
    TEMPORARY_MEMORY_OFF,
)
from .continuity import ConversationContinuity, async_get_continuity
from .conversation_archive import async_get_archive
from .function_groups import assemble_function_tools, get_function_group_runtime
from .functions import FUNCTIONS
from .functions.security import FunctionSecurity, classify_tool
from .guest_mode import (
    async_get_guest_mode,
    guest_policy_editor_snapshot,
    resolve_guest_policy,
)
from .helpers import get_exposed_entities
from .knowledge import (
    async_get_knowledge,
    get_loaded_knowledge,
    knowledge_source_as_dict,
)
from .local_intents import CONF_LOCAL_INTENT_EXCLUSIONS, local_handling_snapshot
from .memory import (
    ANONYMOUS_USER_ID,
    async_get_memory,
    get_memory_mode,
    memory_as_dict,
    memory_enabled,
)
from .prompt import render_effective_prompt
from .request import (
    CONTINUE_CONVERSATION_TOOL,
    assemble_integration_function_tools,
    build_provider_request_snapshot,
    canonical_json,
    format_function_tools,
)
from .request_rules import (
    async_get_request_rules,
    get_request_rule_runtime,
    rule_has_sensitive_actions,
    validate_rule,
)
from .scope import SHARED_HOUSEHOLD_SCOPE_ID, user_scope
from .secret_redaction import redact_secrets, restore_redacted_secrets
from .skills import SkillManager
from .speech import process_speech_text
from .temporary_memory import (
    async_get_temporary_memory,
    async_read_temporary_memory_snapshot,
    get_loaded_temporary_memory,
    temporary_memory_as_dict,
)
from .usage import async_get_usage

WS_COMMAND = f"{DOMAIN}/management"
_UI_SETUP = f"{DOMAIN}.management_ui_setup"


def _reset_request_rule_runtime(
    hass: HomeAssistant, entry_id: str, subentry_id: str, continuity_key: str
) -> None:
    """Clear Request Rule routing state for one ended continuity session."""
    get_request_rule_runtime(hass, entry_id, subentry_id).reset(
        f"continuity:{continuity_key}"
    )


MANAGEMENT_FRONTEND_MODULES = (
    "management-panel.js",
    "agent-config-editor.js",
    "agent-config-help.js",
    "frontend-navigation.js",
    "guest-mode-ui.js",
    "guide-content.js",
    "guide-page.js",
    "overview-page.js",
    "usage-chart.js",
    "request-rules-ui.js",
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


async def _async_preview_effective_request(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    options: dict[str, Any],
    user_id: str,
) -> dict[str, Any]:
    """Assemble a side-effect-free snapshot of a fresh provider request."""
    configured_tools = configured_function_tools_from_data(options)
    guest_manager = await async_get_guest_mode(
        hass, entry.entry_id, subentry.subentry_id
    )
    guest_policy = resolve_guest_policy(hass, options, guest_manager, configured_tools)
    temporary_memories = []
    notes = [
        "User input and conversation history are excluded.",
        "Query-derived persistent memories are excluded because there is no user query.",
    ]
    temporary = get_loaded_temporary_memory(hass, entry.entry_id, subentry.subentry_id)
    temporary_mode = options.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
    scope = user_scope(user_id, source="management_preview")
    temporary_scope, _label = ConversationContinuity.identity_key(
        options.get(CONF_CONVERSATION_CONTINUITY, DEFAULT_CONVERSATION_CONTINUITY),
        scope,
        None,
    )
    if (
        temporary_mode != TEMPORARY_MEMORY_OFF
        and temporary_scope is not None
        and guest_policy.temporary_memory
    ):
        temporary_memories = (
            await temporary.async_active_snapshot(temporary_scope)
            if temporary is not None
            else await async_read_temporary_memory_snapshot(
                hass, entry.entry_id, subentry.subentry_id, temporary_scope
            )
        )
    elif temporary_mode != TEMPORARY_MEMORY_OFF:
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
        if skill_manager is not None and guest_policy.skills
        else []
    )
    knowledge = get_loaded_knowledge(hass, entry.entry_id, subentry.subentry_id)
    knowledge_available = bool(
        guest_policy.knowledge_access
        and options.get(CONF_KNOWLEDGE_ENABLED)
        and knowledge is not None
        and knowledge.source_count > 0
    )
    try:
        exposed_entities = get_exposed_entities(hass)
        if guest_policy.guest_active:
            exposed_entities = [
                entity
                for entity in exposed_entities
                if guest_policy.allows_entity_read(str(entity.get("entity_id", "")))
            ]
        preview = render_effective_prompt(
            hass,
            options,
            exposed_entities=exposed_entities,
            current_device_id=None,
            user_input=None,
            skills=skills,
            memories=None,
            temporary_memories=temporary_memories,
            knowledge_available=knowledge_available,
            guest_policy=guest_policy,
        )
        groups = validate_function_groups(
            options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS),
            configured_tools,
        )
        if guest_policy.guest_active:
            membership = {
                name: group for group in groups for name in group.get("functions", [])
            }
            configured_tools = [
                tool
                for tool in configured_tools
                if guest_policy.allows_configured_tool(tool["spec"]["name"])
                and (
                    tool["spec"]["name"] not in membership
                    or membership[tool["spec"]["name"]].get("guest_allowed") is True
                )
            ]
            allowed_names = {tool["spec"]["name"] for tool in configured_tools}
            groups = [
                {
                    **group,
                    "functions": [
                        name for name in group["functions"] if name in allowed_names
                    ],
                }
                for group in groups
                if group.get("guest_allowed") is True
            ]
        grouped = assemble_function_tools(configured_tools, groups, set())
        provider = build_provider_request_snapshot(options, getattr(entry, "data", {}))
        custom_tools = [
            tool
            for tool in grouped.tools
            if tool.get("spec", {}).get("name") != FUNCTION_GROUP_LOADER_TOOL_NAME
        ]
        loader_tools = [
            tool
            for tool in grouped.tools
            if tool.get("spec", {}).get("name") == FUNCTION_GROUP_LOADER_TOOL_NAME
        ]
        configured_names = {
            tool.get("spec", {}).get("name")
            for tool in configured_tools
            if isinstance(tool, dict)
        }
        integration_tools = assemble_integration_function_tools(
            options,
            configured_names,
            memory_scope_available=memory_enabled(options),
            temporary_scope_available=(
                temporary_mode != TEMPORARY_MEMORY_OFF and temporary_scope is not None
            ),
            knowledge_available=knowledge_available,
            guest_policy=guest_policy,
        )
        conditional_continue = (
            options.get(CONF_CONTINUE_CONVERSATION, DEFAULT_CONTINUE_CONVERSATION)
            == CONTINUE_CONVERSATION_CONDITIONAL
        )
        if conditional_continue:
            integration_tools.append(CONTINUE_CONVERSATION_TOOL)

        formatted_custom = format_function_tools(custom_tools, provider.api_mode)
        formatted_loader = format_function_tools(loader_tools, provider.api_mode)
        formatted_integration = format_function_tools(
            integration_tools, provider.api_mode
        )
        formatted_provider = (
            list(provider.provider_tools) if guest_policy.web_search else []
        )
        request_settings = {"api_mode": provider.api_mode, **provider.api_kwargs}
        if (
            formatted_custom
            or formatted_loader
            or formatted_integration
            or formatted_provider
        ):
            request_settings["tool_choice"] = (
                "required" if conditional_continue else "auto"
            )

        section_values = (
            ("system_context", "System / context", preview.text, "text"),
            (
                "function_tools",
                "Function tools",
                canonical_json(formatted_custom),
                "json",
            ),
            (
                "function_group_loader",
                "Function Group catalogue / loader",
                canonical_json(formatted_loader),
                "json",
            ),
            (
                "integration_tools",
                "Integration tools",
                canonical_json(formatted_integration),
                "json",
            ),
            (
                "provider_tools",
                "Provider tools",
                canonical_json(formatted_provider),
                "json",
            ),
            (
                "request_settings",
                "Request settings",
                canonical_json(request_settings),
                "json",
            ),
        )
        sections = [
            {
                "key": key,
                "label": label,
                "content": content,
                "format": output_format,
                "character_count": len(content),
            }
            for key, label, content, output_format in section_values
        ]

        enabled_tools = [
            tool for tool in configured_tools if function_tool_enabled(tool)
        ]
        grouped_payload = canonical_json(
            format_function_tools(grouped.tools, provider.api_mode)
        )
        ungrouped_payload = canonical_json(
            format_function_tools(enabled_tools, provider.api_mode)
        )
        raw_savings = len(ungrouped_payload) - len(grouped_payload)
        savings = max(0, raw_savings)
        savings_percent = (
            round((savings / len(ungrouped_payload)) * 100)
            if savings and ungrouped_payload
            else 0
        )
    except Exception as err:
        concise = " ".join(str(err).split())[:500] or type(err).__name__
        raise HomeAssistantError(
            f"The effective request could not be assembled: {concise}"
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
        "prompt_sections": [
            {
                "key": section.key,
                "label": section.label,
                "volatility": section.volatility,
            }
            for section in preview.sections
        ],
        "guest_mode": {
            "status": guest_manager.status(),
            "policy": guest_policy.as_diagnostics(),
        },
        "sections": sections,
        "total_character_count": sum(
            section["character_count"] for section in sections
        ),
        "function_group_savings": {
            "characters": savings,
            "percent": savings_percent,
            "grouped_characters": len(grouped_payload),
            "without_on_demand_grouping_characters": len(ungrouped_payload),
        },
        "notes": notes,
    }


async def _async_preview_effective_prompt(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    options: dict[str, Any],
    user_id: str,
) -> dict[str, Any]:
    """Backward-compatible internal alias for the expanded request preview."""
    return await _async_preview_effective_request(
        hass, entry, subentry, options, user_id
    )


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


def _agent_config_revision(data: Mapping[str, Any], title: str) -> str:
    """Return a stable optimistic-concurrency token for one saved agent config."""
    payload = canonical_json(
        {"title": title, "config": agent_config_snapshot(dict(data))}
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _require_agent_config_revision(subentry: Any, expected_revision: Any) -> None:
    """Reject a stale management writer before it can replace newer settings."""
    if expected_revision is None:
        return
    if not isinstance(expected_revision, str):
        raise HomeAssistantError("revision must be a string")
    if expected_revision != _agent_config_revision(subentry.data, subentry.title):
        raise HomeAssistantError(
            "Configuration changed in another tab. Reload the latest saved settings before saving."
        )


def _persist_function_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    extra_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist only Function Tool fields against the latest saved subentry."""
    updates: dict[str, Any] = {
        CONF_FUNCTION_TOOLS: tools,
        CONF_FUNCTION_GROUPS: groups,
    }
    if extra_updates:
        updates.update(extra_updates)
    normalized = preserve_legacy_guest_policy(
        subentry.data,
        merge_agent_config(subentry.data, updates),
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=normalized)
    snapshot = agent_config_snapshot(normalized)
    return {
        "functions": snapshot[CONF_FUNCTION_TOOLS],
        "function_groups": snapshot[CONF_FUNCTION_GROUPS],
    }


async def _function_reference_state(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    subentry_data: MappingProxyType | dict[str, Any],
    function_name: str,
):
    """Return exact durable references to one configured Function Tool."""
    rules = await async_get_request_rules(hass, entry_id, subentry_id)
    guest_names = subentry_data.get(CONF_GUEST_ALLOWED_FUNCTION_NAMES, [])
    return rules, {
        "request_rules": rules.function_references(function_name),
        "guest_mode": isinstance(guest_names, list) and function_name in guest_names,
    }


def _function_reference_error(name: str, references: dict[str, Any]) -> str:
    """Describe semantic references that must be resolved before deletion."""
    parts: list[str] = []
    rule_names = [
        item.get("name", item.get("id", "unnamed rule"))
        for item in references.get("request_rules", [])
    ]
    if rule_names:
        parts.append("Request Rules: " + ", ".join(rule_names))
    if references.get("guest_mode"):
        parts.append("Guest Mode custom function access")
    return (
        f"Function Tool `{name}` is still referenced by "
        + "; ".join(parts)
        + ". Update those references before deleting it."
    )


def _redact_export_secrets(value: Any, *, schema: bool = False) -> Any:
    """Redact credential values while preserving exported configuration structure."""
    return redact_secrets(value, schema=schema)


def _export_agent(subentry) -> dict[str, Any]:
    """Build a versioned configuration document with best-effort redaction."""
    snapshot = preserve_legacy_guest_policy(
        dict(subentry.data), agent_config_snapshot(subentry.data)
    )
    return {
        "schema": "extended_openai_conversation.agent",
        "version": AGENT_CONFIG_EXPORT_VERSION,
        "title": subentry.title,
        "config": _redact_export_secrets(snapshot),
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
    config = restore_redacted_secrets(value.get("config"))
    if not isinstance(config, dict):
        raise AgentConfigError("config", "must be an object")
    return {
        "title": validate_agent_title(
            value.get("title"), default="Imported conversation agent"
        ),
        "config": preserve_legacy_guest_policy(config, normalize_agent_config(config)),
    }


def _prepare_request_rule(value: Any, rule_id: str | None = None) -> dict[str, Any]:
    """Assign/preserve rule identity before canonical validation."""
    if not isinstance(value, Mapping):
        raise HomeAssistantError("rule must be an object")
    raw = dict(value)
    if rule_id is not None:
        raw["id"] = rule_id
    elif not isinstance(raw.get("id"), str) or not str(raw["id"]).strip():
        raw["id"] = uuid4().hex
    return validate_rule(raw)


def _validate_request_rule_functions(
    rule: Mapping[str, Any], configured_tools: list[dict[str, Any]]
) -> None:
    """Validate configured Function references in canonical persisted actions."""
    service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
    calls: list[Mapping[str, Any]] = []
    for action in rule.get("action", {}).get("actions", []):
        if not isinstance(action, Mapping):
            continue
        if action.get("action", action.get("service")) != service_action:
            continue
        data = action.get("data")
        if not isinstance(data, Mapping) or not isinstance(data.get("function"), str):
            raise HomeAssistantError("Configured Function action is invalid")
        arguments = data.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise HomeAssistantError("Configured Function arguments must be an object")
        calls.append(data)

    available = {
        tool["spec"]["name"] for tool in configured_tools if function_tool_enabled(tool)
    }
    missing = {
        str(call["function"]) for call in calls if call["function"] not in available
    }
    if missing:
        raise HomeAssistantError(
            "Function Tool is unavailable or disabled: " + ", ".join(sorted(missing))
        )
    by_name = {tool["spec"]["name"]: tool for tool in configured_tools}
    for call in calls:
        function_name = str(call["function"])
        parameters = by_name[function_name]["spec"].get("parameters", {})
        required = (
            parameters.get("required", []) if isinstance(parameters, dict) else []
        )
        missing_inputs = set(required) - set(call.get("arguments", {}))
        if missing_inputs:
            raise HomeAssistantError(
                f"Function Tool `{function_name}` needs input: "
                + ", ".join(sorted(missing_inputs))
            )


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
                guest_mode = await async_get_guest_mode(
                    hass, entry.entry_id, subentry.subentry_id
                )
                configured_tools = configured_function_tools_from_data(subentry.data)
                guest_status = guest_mode.status()
                guest_status["has_home_assistant_exclusions"] = any(
                    subentry.data.get(key)
                    for key in (
                        "guest_excluded_labels",
                        "guest_excluded_areas",
                        "guest_excluded_domains",
                        "guest_excluded_entities",
                    )
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
                        "function_count": sum(
                            function_tool_enabled(tool) for tool in configured_tools
                        ),
                        "function_group_count": len(
                            subentry.data.get(
                                CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS
                            )
                        ),
                        "archive_enabled": bool(
                            subentry.data.get(
                                CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED
                            )
                        ),
                        "tokens_today": usage.today_summary()["total_tokens"],
                        "guest_mode": guest_status,
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

    if section == "request_rules":
        _require_admin(is_admin)
        rules = await async_get_request_rules(hass, entry_id, subentry_id)
        if action == "list":
            snapshot = rules.snapshot()
            snapshot["rules"] = [
                {**rule, "sensitive_matching_warning": rule_has_sensitive_actions(rule)}
                for rule in snapshot["rules"]
            ]
            configured_tools = (
                configured_function_tools_from_data(subentry.data)
                if hasattr(subentry, "data")
                else []
            )
            snapshot["function_catalog"] = [
                {
                    "name": tool["spec"]["name"],
                    "description": tool["spec"].get("description", ""),
                    "parameters": tool["spec"].get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
                for tool in configured_tools
                if function_tool_enabled(tool)
            ]
            return snapshot
        if action == "test":
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                raise HomeAssistantError("Test request text is required")
            registry_entry = next(
                (
                    item
                    for item in er.async_get(hass).entities.values()
                    if item.config_entry_id == entry_id
                    and item.config_subentry_id == subentry_id
                    and item.domain == "conversation"
                ),
                None,
            )
            if registry_entry is None:
                raise HomeAssistantError("Conversation agent entity is not available")
            return cast(
                dict[str, Any],
                await hass.services.async_call(
                    DOMAIN,
                    "process",
                    {"text": text.strip(), "agent_id": registry_entry.entity_id},
                    blocking=True,
                    context=Context(user_id=user_id),
                    return_response=True,
                ),
            )
        if action == "defaults":
            defaults = await rules.async_set_defaults(
                message.get("defaults"), expected_revision=message.get("revision")
            )
            return {"defaults": defaults, "revision": rules.revision()}
        if action == "wording_groups":
            wording_groups = await rules.async_set_wording_groups(
                message.get("wording_groups"),
                expected_revision=message.get("revision"),
            )
            return {"wording_groups": wording_groups, "revision": rules.revision()}
        if action == "create":
            candidate = _prepare_request_rule(message.get("rule"))
            _validate_request_rule_functions(
                candidate,
                configured_function_tools_from_data(subentry.data)
                if hasattr(subentry, "data")
                else [],
            )
            rule = await rules.async_create(
                candidate, expected_revision=message.get("revision")
            )
            return {"rule": rule, "revision": rules.revision()}
        rule_id = message.get("rule_id")
        if not isinstance(rule_id, str):
            raise HomeAssistantError("rule_id is required")
        if action == "update":
            candidate = _prepare_request_rule(message.get("rule"), rule_id)
            _validate_request_rule_functions(
                candidate,
                configured_function_tools_from_data(subentry.data)
                if hasattr(subentry, "data")
                else [],
            )
            rule = await rules.async_update(
                rule_id, candidate, expected_revision=message.get("revision")
            )
            return {"rule": rule, "revision": rules.revision()}
        if action == "delete":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            deleted = await rules.async_delete(
                rule_id, expected_revision=message.get("revision")
            )
            return {"deleted": deleted, "revision": rules.revision()}
        if action == "duplicate":
            rule = await rules.async_duplicate(
                rule_id, expected_revision=message.get("revision")
            )
            return {"rule": rule, "revision": rules.revision()}
        raise HomeAssistantError(f"Unknown Request Rules action: {action}")

    if section == "guest_mode":
        guest_manager = await async_get_guest_mode(hass, entry_id, subentry_id)
        if action == "get":
            configured_tools = configured_function_tools_from_data(subentry.data)
            policy = resolve_guest_policy(
                hass, subentry.data, guest_manager, configured_tools
            )
            if not is_admin:
                return {
                    "status": guest_manager.status(),
                    "policy": policy.as_diagnostics(),
                }
            library = await async_get_knowledge(hass, entry_id, subentry_id)
            groups = validate_function_groups(
                subentry.data.get(CONF_FUNCTION_GROUPS, []), configured_tools
            )
            return {
                "status": guest_manager.status(),
                "policy": policy.as_diagnostics(),
                "config": guest_policy_editor_snapshot(
                    hass, subentry.data, configured_tools
                ),
                "legacy_policy": subentry.data.get(CONF_GUEST_POLICY_VERSION)
                != GUEST_POLICY_VERSION,
                "migration_notice": (
                    "This agent still uses the legacy Guest allow-list. Review the "
                    "conservative exclusion draft below; the legacy policy remains "
                    "enforced until you save."
                    if subentry.data.get(CONF_GUEST_POLICY_VERSION)
                    != GUEST_POLICY_VERSION
                    else None
                ),
                "knowledge_sources": await library.async_list(),
                "functions": [
                    {
                        "name": tool["spec"]["name"],
                        "description": tool["spec"].get("description", ""),
                        "enabled": function_tool_enabled(tool),
                        "unsafe_in_guest_mode": classify_tool(tool)
                        > FunctionSecurity.CONTROL,
                    }
                    for tool in configured_tools
                ],
                "function_groups": [
                    {
                        "id": group["id"],
                        "name": group["name"],
                        "description": group["description"],
                        "functions": group["functions"],
                    }
                    for group in groups
                ],
                "domains": sorted(
                    {
                        item["entity_id"].partition(".")[0]
                        for item in get_exposed_entities(hass)
                        if isinstance(item.get("entity_id"), str)
                    }
                ),
            }
        _require_admin(is_admin)
        if action == "save_policy":
            updates = message.get("config")
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            guest_fields = GUEST_V2_FIELDS | {CONF_GUEST_MODE_ENABLED}
            if set(updates) - guest_fields:
                raise HomeAssistantError("Guest policy contains unknown fields")
            updates[CONF_GUEST_POLICY_VERSION] = GUEST_POLICY_VERSION
            normalized = merge_agent_config(subentry.data, updates)
            hass.config_entries.async_update_subentry(entry, subentry, data=normalized)
            configured_tools = configured_function_tools_from_data(normalized)
            return {
                "config": guest_policy_editor_snapshot(
                    hass, normalized, configured_tools
                )
            }
        if action == "update":
            return {
                "status": await guest_manager.async_update_trusted(
                    active_from=message.get("active_from"),
                    active_until=message.get("active_until"),
                    indefinite=message.get("indefinite") is True,
                )
            }
        if action == "disable":
            return {"status": await guest_manager.async_disable_trusted()}

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
                "revision": _agent_config_revision(subentry.data, subentry.title),
                "config": config,
                "defaults": agent_config_snapshot(agent_config_defaults()),
                "options": agent_config_options(),
                "model_capabilities": model_capabilities(config[CONF_CHAT_MODEL]),
                "function_types": sorted(FUNCTIONS),
                "local_handling": local_handling_snapshot(
                    hass,
                    entry_id,
                    subentry_id,
                    config.get(CONF_LOCAL_INTENT_EXCLUSIONS, []),
                ),
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
            _require_agent_config_revision(subentry, message.get("revision"))
            normalized = merge_agent_config(subentry.data, updates)
            if CONF_GUEST_POLICY_VERSION not in subentry.data:
                # The general configuration editor must not implicitly accept
                # the v2 Guest migration draft. Only Guest Mode's explicit save
                # action crosses this boundary.
                for key in GUEST_V2_FIELDS:
                    normalized.pop(key, None)
            requested_title = message.get("title")
            saved_title = (
                validate_agent_title(requested_title)
                if requested_title is not None
                else validate_agent_title(subentry.title)
            )
            hass.config_entries.async_update_subentry(
                entry, subentry, data=normalized, title=saved_title
            )
            snapshot = agent_config_snapshot(normalized)
            return {
                "title": saved_title,
                "revision": _agent_config_revision(normalized, saved_title),
                "config": snapshot,
                "model_capabilities": model_capabilities(snapshot[CONF_CHAT_MODEL]),
                "local_handling": local_handling_snapshot(
                    hass,
                    entry_id,
                    subentry_id,
                    snapshot.get(CONF_LOCAL_INTENT_EXCLUSIONS, []),
                ),
            }
        if action == "duplicate":
            _require_admin(is_admin)
            requested_title = message.get("title")
            if requested_title is None:
                suffix = " - Copy"
                source = validate_agent_title(subentry.title)
                base = source[: MAX_AGENT_TITLE_LENGTH - len(suffix)].rstrip()
                title = validate_agent_title(f"{base}{suffix}")
            else:
                title = validate_agent_title(requested_title)
            duplicate_source = {
                key: value
                for key, value in subentry.data.items()
                if key in AGENT_CONFIG_FIELDS
            }
            duplicate = ConfigSubentry(
                data=MappingProxyType(
                    preserve_legacy_guest_policy(
                        duplicate_source, normalize_agent_config(duplicate_source)
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
                _require_agent_config_revision(subentry, message.get("revision"))
                hass.config_entries.async_update_subentry(
                    entry, subentry, data=parsed["config"], title=parsed["title"]
                )
                return {
                    "status": "updated",
                    "subentry_id": subentry.subentry_id,
                    "revision": _agent_config_revision(
                        parsed["config"], parsed["title"]
                    ),
                }
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
        if action in {"prompt_preview", "request_preview"}:
            updates = message.get("config", {})
            if not isinstance(updates, dict):
                raise HomeAssistantError("config must be an object")
            normalized = merge_agent_config(subentry.data, updates)
            return await _async_preview_effective_request(
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
        tools = configured_function_tools_from_data(subentry.data)
        groups = validate_function_groups(
            subentry.data.get(CONF_FUNCTION_GROUPS, []), tools
        )
        if action == "validate_current":
            return {"valid": True, "errors": {}}
        if action == "save":
            tool_candidate = message.get("tool")
            if not isinstance(tool_candidate, dict):
                raise HomeAssistantError("tool must be an object")
            saved_tool = validate_function_tools([tool_candidate])[0]
            original_name = message.get("original_name")
            if original_name is not None and not isinstance(original_name, str):
                raise HomeAssistantError("original_name must be a string")
            saved_name = saved_tool["spec"]["name"]
            existing_index = next(
                (
                    index
                    for index, tool in enumerate(tools)
                    if tool["spec"]["name"] == original_name
                ),
                None,
            )
            if original_name is not None and existing_index is None:
                raise HomeAssistantError("The Function Tool no longer exists")
            if any(
                tool["spec"]["name"] == saved_name and index != existing_index
                for index, tool in enumerate(tools)
            ):
                raise HomeAssistantError(f"Function Tool {saved_name} already exists")
            if existing_index is None:
                tools.append(saved_tool)
                return _persist_function_configuration(
                    hass, entry, subentry, tools, groups
                )

            tools[existing_index] = saved_tool
            if original_name == saved_name:
                return _persist_function_configuration(
                    hass, entry, subentry, tools, groups
                )

            assert original_name is not None
            original_tools = configured_function_tools_from_data(subentry.data)
            original_groups = [dict(group) for group in groups]
            groups = [
                {
                    **group,
                    "functions": [
                        saved_name if name == original_name else name
                        for name in group["functions"]
                    ],
                }
                for group in groups
            ]
            rules, references = await _function_reference_state(
                hass, entry_id, subentry_id, subentry.data, original_name
            )
            guest_names = list(subentry.data.get(CONF_GUEST_ALLOWED_FUNCTION_NAMES, []))
            renamed_guest_names = [
                saved_name if name == original_name else name for name in guest_names
            ]
            result = _persist_function_configuration(
                hass,
                entry,
                subentry,
                tools,
                groups,
                extra_updates={CONF_GUEST_ALLOWED_FUNCTION_NAMES: renamed_guest_names},
            )
            try:
                renamed_rule_references = await rules.async_rename_function_reference(
                    original_name, saved_name
                )
            except Exception:
                _persist_function_configuration(
                    hass,
                    entry,
                    subentry,
                    original_tools,
                    original_groups,
                    extra_updates={CONF_GUEST_ALLOWED_FUNCTION_NAMES: guest_names},
                )
                raise
            result["renamed_references"] = {
                "request_rules": renamed_rule_references,
                "guest_mode": references["guest_mode"],
            }
            return result
        if action == "set_enabled":
            name = message.get("name")
            enabled = message.get("enabled")
            if not isinstance(name, str) or not isinstance(enabled, bool):
                raise HomeAssistantError("name and enabled are required")
            tool = next((item for item in tools if item["spec"]["name"] == name), None)
            if tool is None:
                raise HomeAssistantError("The Function Tool no longer exists")
            tool["enabled"] = enabled
            result = _persist_function_configuration(
                hass, entry, subentry, tools, groups
            )
            if not enabled:
                _rules, references = await _function_reference_state(
                    hass, entry_id, subentry_id, subentry.data, name
                )
                result["references"] = references
            return result
        if action == "delete":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            name = message.get("name")
            if not isinstance(name, str):
                raise HomeAssistantError("name is required")
            remaining = [tool for tool in tools if tool["spec"]["name"] != name]
            if len(remaining) == len(tools):
                raise HomeAssistantError("The Function Tool no longer exists")
            _rules, references = await _function_reference_state(
                hass, entry_id, subentry_id, subentry.data, name
            )
            if references["request_rules"] or references["guest_mode"]:
                raise HomeAssistantError(_function_reference_error(name, references))
            groups = [
                {
                    **group,
                    "functions": [item for item in group["functions"] if item != name],
                }
                for group in groups
            ]
            return _persist_function_configuration(
                hass, entry, subentry, remaining, groups
            )
        if action == "save_group":
            group_candidate = message.get("group")
            if not isinstance(group_candidate, dict):
                raise HomeAssistantError("group must be an object")
            candidate = group_candidate
            candidate_functions = candidate.get("functions", [])
            if not isinstance(candidate_functions, list) or not all(
                isinstance(name, str) for name in candidate_functions
            ):
                raise HomeAssistantError("group functions must be a list of names")
            original_id = message.get("original_id")
            if original_id is not None and not isinstance(original_id, str):
                raise HomeAssistantError("original_id must be a string")
            existing = next(
                (group for group in groups if group["id"] == original_id), None
            )
            if original_id is not None and existing is None:
                raise HomeAssistantError("The Function Group no longer exists")
            selected = set(candidate_functions)
            remaining_groups = [
                {
                    **group,
                    "functions": [
                        name for name in group["functions"] if name not in selected
                    ],
                }
                for group in groups
                if group["id"] != original_id
            ]
            validated_groups = validate_function_groups(
                [*remaining_groups, candidate], tools
            )
            return _persist_function_configuration(
                hass, entry, subentry, tools, validated_groups
            )
        if action == "delete_group":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            group_id = message.get("group_id")
            if not isinstance(group_id, str):
                raise HomeAssistantError("group_id is required")
            remaining = [group for group in groups if group["id"] != group_id]
            if len(remaining) == len(groups):
                raise HomeAssistantError("The Function Group no longer exists")
            return _persist_function_configuration(
                hass, entry, subentry, tools, remaining
            )

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

    if section == "service_catalog" and action == "get":
        _require_admin(is_admin)
        return {"services": await service_helper.async_get_all_descriptions(hass)}

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
            continuity_key = message.get("continuity_key")
            if not isinstance(continuity_key, str):
                raise HomeAssistantError("continuity_key is required")
            ended = await continuity.async_end(continuity_key)
            if ended:
                function_groups = get_function_group_runtime(
                    hass, entry_id, subentry_id
                )
                if function_groups is not None:
                    function_groups.end(f"continuity:{continuity_key}")
                _reset_request_rule_runtime(hass, entry_id, subentry_id, continuity_key)
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
            knowledge_source = await library.async_create(
                message.get("title", ""),
                message.get("description", ""),
                message.get("content", ""),
            )
            return {
                "status": "created",
                "source": knowledge_source_as_dict(knowledge_source),
            }
        if action == "update":
            knowledge_source = await library.async_update(
                str(message.get("source_id", "")),
                message.get("title"),
                message.get("description"),
                message.get("content"),
            )
            return {
                "status": "updated",
                "source": knowledge_source_as_dict(knowledge_source),
            }
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
        vol.Optional("group"): dict,
        vol.Optional("name"): str,
        vol.Optional("original_name"): str,
        vol.Optional("original_id"): str,
        vol.Optional("group_id"): str,
        vol.Optional("rule_id"): str,
        vol.Optional("rule"): dict,
        vol.Optional("pin"): str,
        vol.Optional("pin_repeat"): str,
        vol.Optional("text"): str,
        vol.Optional("defaults"): dict,
        vol.Optional("enabled"): bool,
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
        vol.Optional("active_from"): str,
        vol.Optional("active_until"): str,
        vol.Optional("indefinite"): bool,
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
