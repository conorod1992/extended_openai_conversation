"""Diagnostics for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .agent_config import validate_function_groups, validate_function_tools
from .const import (
    CONF_ARCHIVE_ENABLED,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_TEMPORARY_MEMORY,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_CONVERSATION_CONTINUITY,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_KNOWLEDGE_ENABLED,
    DEFAULT_MEMORY_AUTO_CREATE,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_MEMORY_ENABLED,
    DEFAULT_MEMORY_RETRIEVAL_MODE,
    DEFAULT_TEMPORARY_MEMORY,
)
from .continuity import async_get_continuity
from .conversation_archive import async_get_archive
from .function_groups import get_function_group_runtime
from .guest_mode import async_get_guest_mode, resolve_guest_policy
from .knowledge import async_get_knowledge
from .memory import async_get_memory, get_memory_mode
from .temporary_memory import async_get_temporary_memory
from .usage import async_get_usage


def _configured_function_tools(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Parse configured tools with the same missing-value fallback as execution."""
    function_tools_config = data.get(CONF_FUNCTION_TOOLS)
    return validate_function_tools(
        yaml.safe_load(function_tools_config)
        if function_tools_config
        else DEFAULT_CONF_FUNCTION_TOOLS
    )


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return non-sensitive persistent-memory diagnostics."""
    agents: list[dict[str, Any]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        diagnostics: dict[str, Any] = {
            "subentry_id": subentry.subentry_id,
            "memory_mode": get_memory_mode(subentry.data),
            "memory_enabled": subentry.data.get(
                CONF_MEMORY_ENABLED, DEFAULT_MEMORY_ENABLED
            ),
            "automatic_memory_creation": subentry.data.get(
                CONF_MEMORY_AUTO_CREATE, DEFAULT_MEMORY_AUTO_CREATE
            ),
            "automatic_retrieval_limit": subentry.data.get(
                CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
                DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
            ),
            "memory_retrieval_mode": subentry.data.get(
                CONF_MEMORY_RETRIEVAL_MODE, DEFAULT_MEMORY_RETRIEVAL_MODE
            ),
            "knowledge_enabled": subentry.data.get(
                CONF_KNOWLEDGE_ENABLED, DEFAULT_KNOWLEDGE_ENABLED
            ),
            "archive_enabled": subentry.data.get(
                CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED
            ),
            "continuity_mode": subentry.data.get(
                CONF_CONVERSATION_CONTINUITY, DEFAULT_CONVERSATION_CONTINUITY
            ),
            "temporary_memory_mode": subentry.data.get(
                CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY
            ),
        }
        diagnostics.update(
            async_get_continuity(hass, entry.entry_id, subentry.subentry_id).stats()
        )
        try:
            function_tools = _configured_function_tools(subentry.data)
            function_groups = validate_function_groups(
                subentry.data.get(CONF_FUNCTION_GROUPS, list(DEFAULT_FUNCTION_GROUPS)),
                function_tools,
            )
            diagnostics.update(
                {
                    "configured_function_tools": len(function_tools),
                    "configured_function_groups": len(function_groups),
                    "configured_on_demand_function_groups": sum(
                        group["loading_mode"] == "on_demand"
                        for group in function_groups
                    ),
                }
            )
            runtime = get_function_group_runtime(
                hass, entry.entry_id, subentry.subentry_id
            )
            if runtime is not None:
                diagnostics.update(runtime.stats())
            guest_mode = await async_get_guest_mode(
                hass, entry.entry_id, subentry.subentry_id
            )
            diagnostics["guest_mode"] = {
                "status": guest_mode.status(),
                "policy": resolve_guest_policy(
                    hass, subentry.data, guest_mode, function_tools
                ).as_diagnostics(),
            }
        except Exception as err:
            diagnostics["function_group_configuration_error"] = type(err).__name__
        try:
            temporary = await async_get_temporary_memory(
                hass, entry.entry_id, subentry.subentry_id
            )
            diagnostics.update(temporary.stats())
        except Exception as err:
            diagnostics["temporary_memory_storage_error"] = type(err).__name__
        try:
            memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
            diagnostics.update(memory.stats())
        except Exception as err:
            diagnostics["storage_error"] = type(err).__name__
        try:
            knowledge = await async_get_knowledge(
                hass, entry.entry_id, subentry.subentry_id
            )
            diagnostics.update(knowledge.stats())
        except Exception as err:
            diagnostics["knowledge_storage_error"] = type(err).__name__
        try:
            archive = await async_get_archive(
                hass, entry.entry_id, subentry.subentry_id
            )
            diagnostics["archive"] = archive.stats()
        except Exception as err:
            diagnostics["archive_storage_error"] = type(err).__name__
        try:
            usage = await async_get_usage(hass, entry.entry_id, subentry.subentry_id)
            diagnostics["usage"] = usage.as_dict()
        except Exception as err:
            diagnostics["usage_storage_error"] = type(err).__name__
        agents.append(diagnostics)
    return {"conversation_agents": agents}
