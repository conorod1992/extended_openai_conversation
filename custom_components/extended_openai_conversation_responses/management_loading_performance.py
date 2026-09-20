"""Management navigation catalogs, bounded Overview data and config snapshots."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant

from .agent_config import (
    AGENT_CONFIG_FIELDS,
    agent_config_defaults,
    function_tool_enabled,
    validate_function_groups,
    validate_function_tools,
)
from .const import (
    CONF_API_PROVIDER,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    DEFAULT_API_PROVIDER,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_CHAT_MODEL,
    DEFAULT_FUNCTION_GROUPS,
    DOMAIN,
)
from .conversation_archive import async_get_archive
from .feature_status import management_feature_status
from .guest_mode import async_get_guest_mode, get_loaded_guest_mode
from .knowledge import async_get_knowledge, get_loaded_knowledge
from .management_function_repair import function_tools_issue as _function_tools_issue
from .management_history_queries import usage_summary
from .management_projections import async_scope_catalog_projection, settings_snapshot
from .management_setup_health import add_setup_health
from .memory import async_get_memory, get_memory_mode
from .usage import async_get_usage


def _guest_has_ha_exclusions(options: dict[str, Any]) -> bool:
    return any(
        options.get(key)
        for key in (
            "guest_excluded_labels",
            "guest_excluded_areas",
            "guest_excluded_domains",
            "guest_excluded_entities",
        )
    )


def _agent_snapshot(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    memory_count: int = 0,
    knowledge_source_count: int = 0,
    tokens_today: int = 0,
    guest_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the cheap frontend metadata shared by bootstrap and overview."""
    options = config if config is not None else dict(subentry.data)
    configured_tools, function_issue = _function_tools_issue(options)
    if guest_status is None:
        loaded_guest = get_loaded_guest_mode(hass, entry.entry_id, subentry.subentry_id)
        guest_status = (
            dict(loaded_guest.status())
            if loaded_guest is not None
            else {"state": "unloaded", "currently_active": False}
        )
    else:
        guest_status = dict(guest_status)
    guest_status["has_home_assistant_exclusions"] = _guest_has_ha_exclusions(options)
    snapshot = {
        "entry_id": entry.entry_id,
        "entry_title": entry.title,
        "subentry_id": subentry.subentry_id,
        "title": title if title is not None else subentry.title,
        "provider": entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER),
        "model": options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
        "memory_mode": get_memory_mode(options),
        "memory_count": memory_count,
        "knowledge_enabled": bool(options.get(CONF_KNOWLEDGE_ENABLED, False)),
        "knowledge_source_count": knowledge_source_count,
        "function_count": sum(function_tool_enabled(tool) for tool in configured_tools),
        "function_group_count": len(
            options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)
        ),
        "archive_enabled": bool(
            options.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        ),
        "tokens_today": tokens_today,
        "guest_mode": guest_status,
    }
    if knowledge_source_count == 0:
        loaded = get_loaded_knowledge(hass, entry.entry_id, subentry.subentry_id)
        if loaded is not None:
            knowledge_source_count = int(loaded.source_count)
            snapshot["knowledge_source_count"] = knowledge_source_count
    snapshot["feature_status"] = management_feature_status(
        options, knowledge_source_count=knowledge_source_count
    )
    if function_issue is not None:
        snapshot["configuration_issue"] = {
            "field": CONF_FUNCTION_TOOLS,
            "message": function_issue,
            "repairable": True,
        }
    return snapshot


async def async_agent_catalog(
    hass: HomeAssistant, user_id: str, is_admin: bool
) -> dict[str, Any]:
    """Return startup navigation metadata without loading memory/archive scopes."""
    del user_id
    agents = [
        _agent_snapshot(hass, entry, subentry)
        for entry in hass.config_entries.async_entries(DOMAIN)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    ]
    return {"agents": agents, "is_admin": is_admin}


async def async_scope_catalog(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    entry_id: str,
    subentry_id: str,
) -> dict[str, Any]:
    """Load scopes for the already-validated Management agent concurrently."""
    memory, archive = await asyncio.gather(
        async_get_memory(hass, entry_id, subentry_id),
        async_get_archive(hass, entry_id, subentry_id),
    )
    return {
        "scopes": await async_scope_catalog_projection(
            hass,
            user_id,
            is_admin,
            memory.scope_counts(),
            archive.scope_counts(),
        )
    }


async def async_overview_summary(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    """Load and bound the already-selected agent's Overview in one request."""
    entry_id = str(entry.entry_id)
    subentry_id = str(subentry.subentry_id)

    usage_result, memory_result, knowledge_result, guest_result = await asyncio.gather(
        async_get_usage(hass, entry_id, subentry_id),
        async_get_memory(hass, entry_id, subentry_id),
        async_get_knowledge(hass, entry_id, subentry_id),
        async_get_guest_mode(hass, entry_id, subentry_id),
        return_exceptions=True,
    )

    load_errors: list[dict[str, str]] = []

    def record_failure(key: str, label: str, error: BaseException) -> None:
        load_errors.append(
            {
                "key": key,
                "label": label,
                "message": str(error) or type(error).__name__,
            }
        )

    usage: dict[str, Any] = {}
    tokens_today = 0
    if isinstance(usage_result, BaseException):
        record_failure("usage", "Usage", usage_result)
    else:
        usage = usage_summary(usage_result)
        if not is_admin:
            usage["latest"] = None
        tokens_today = int(usage["today"].get("total_tokens", 0))

    memory_count = 0
    if isinstance(memory_result, BaseException):
        record_failure("memories", "Memory", memory_result)
    else:
        memory_count = int(memory_result.stats().get("memory_count", 0))

    knowledge_source_count = 0
    if isinstance(knowledge_result, BaseException):
        record_failure("knowledge", "Knowledge", knowledge_result)
    else:
        knowledge_source_count = int(knowledge_result.source_count)

    guest_status: dict[str, Any] | None = None
    if isinstance(guest_result, BaseException):
        record_failure("guest_mode", "Guest Mode", guest_result)
    else:
        guest_status = guest_result.status()

    result = {
        "agent": _agent_snapshot(
            hass,
            entry,
            subentry,
            memory_count=memory_count,
            knowledge_source_count=knowledge_source_count,
            tokens_today=tokens_today,
            guest_status=guest_status,
        ),
        "usage": usage,
        "conversations": settings_snapshot(dict(subentry.data)),
        "load_errors": load_errors,
    }

    return add_setup_health(hass, entry, subentry, result, is_admin=is_admin)


def _snapshot_normalized_configuration(config: dict[str, Any]) -> dict[str, Any]:
    """Build the frontend config shape without normalizing an already-valid config."""
    snapshot = agent_config_defaults()
    snapshot.update(
        {
            key: deepcopy(value)
            for key, value in config.items()
            if key in AGENT_CONFIG_FIELDS
        }
    )
    function_tools = validate_function_tools(snapshot[CONF_FUNCTION_TOOLS])
    snapshot[CONF_FUNCTION_TOOLS] = function_tools
    snapshot[CONF_FUNCTION_GROUPS] = validate_function_groups(
        snapshot[CONF_FUNCTION_GROUPS], function_tools
    )
    return snapshot
