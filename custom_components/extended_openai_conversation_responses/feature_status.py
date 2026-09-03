"""Management-facing effective status for retained-data capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from functools import wraps
from typing import Any

from .const import (
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_SHARED_MEMORY_MODE,
    DEFAULT_KNOWLEDGE_ENABLED,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_SHARED_MEMORY_MODE,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_MANUAL,
    SHARED_MEMORY_DISABLED,
)
from .memory import get_memory_mode

_INSTALLED = False
_FRONTEND_MODULE = "management-feature-status.js"


def management_feature_status(
    options: Mapping[str, Any], *, knowledge_source_count: int
) -> dict[str, dict[str, Any]]:
    """Describe what Memory and Knowledge currently mean for one agent.

    This is intentionally agent-level management status, not a prediction for one
    request. Request scope and Guest Mode remain authoritative runtime gates.
    """
    memory_mode = get_memory_mode(options)
    memory_enabled = memory_mode in {MEMORY_MODE_MANUAL, MEMORY_MODE_AUTOMATIC}
    shared_memory_mode = str(
        options.get(CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE)
    )
    shared_memory_enabled = shared_memory_mode != SHARED_MEMORY_DISABLED
    try:
        auto_retrieve_limit = max(
            0,
            int(
                options.get(
                    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
                    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
                )
            ),
        )
    except (TypeError, ValueError):
        auto_retrieve_limit = DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT

    if not memory_enabled:
        memory_label = "Off"
        memory_summary = (
            "Stored memories are retained, but conversations cannot use persistent "
            "memory."
        )
        memory_detail = (
            "Persistent memory is disabled for this agent. Stored records are retained, "
            "but memory tools and automatic memory context are not available."
        )
        memory_state = "disabled"
    else:
        memory_label = (
            "Automatic" if memory_mode == MEMORY_MODE_AUTOMATIC else "Manual"
        )
        saving = (
            "Automatic saving is allowed"
            if memory_mode == MEMORY_MODE_AUTOMATIC
            else "Only explicit remember requests can save new memories"
        )
        inclusion = (
            f"up to {auto_retrieve_limit} relevant memories can be included automatically"
            if auto_retrieve_limit > 0
            else "automatic memory inclusion is off; memory search tools remain available"
        )
        household = (
            "shared household memory is enabled"
            if shared_memory_enabled
            else "shared household memory is off"
        )
        memory_summary = f"{saving} · {inclusion} · {household}."
        scope_detail = (
            "Personal and shared-household memory can be used when a request resolves "
            "to those retained scopes."
            if shared_memory_enabled
            else "Personal memory can be used, but requests resolved to the shared "
            "household scope cannot use persistent memory."
        )
        memory_detail = (
            f"{saving}. {inclusion.capitalize()}. {scope_detail} Guest Mode and the "
            "resolved request scope can still restrict memory further."
        )
        memory_state = "enabled"

    source_count = max(0, int(knowledge_source_count))
    knowledge_enabled = bool(
        options.get(CONF_KNOWLEDGE_ENABLED, DEFAULT_KNOWLEDGE_ENABLED)
    )
    if not knowledge_enabled:
        knowledge_state = "disabled"
        knowledge_label = "Off"
        knowledge_summary = (
            f"{source_count} stored source{'s' if source_count != 1 else ''} retained; "
            "model access is off."
        )
        knowledge_detail = (
            "Knowledge Library is disabled for this agent. Stored sources are retained, "
            "but Knowledge tools and prompt guidance are not available to conversations."
        )
    elif source_count == 0:
        knowledge_state = "empty"
        knowledge_label = "Needs sources"
        knowledge_summary = "Enabled, but no sources exist yet."
        knowledge_detail = (
            "Knowledge Library is enabled, but its model tools remain unavailable until "
            "at least one source exists."
        )
    else:
        knowledge_state = "available"
        knowledge_label = "Available"
        knowledge_summary = (
            f"{source_count} source{'s' if source_count != 1 else ''} available for "
            "on-demand model search."
        )
        knowledge_detail = (
            f"Knowledge Library is enabled with {source_count} source"
            f"{'s' if source_count != 1 else ''}. The model can search and retrieve "
            "them on demand; Guest Mode can still restrict access for guest requests."
        )

    return {
        "memory": {
            "state": memory_state,
            "label": memory_label,
            "summary": memory_summary,
            "detail": memory_detail,
            "enabled": memory_enabled,
            "mode": memory_mode,
            "automatic_inclusion_limit": auto_retrieve_limit,
            "automatic_inclusion_enabled": auto_retrieve_limit > 0,
            "shared_memory_enabled": shared_memory_enabled,
            "shared_memory_mode": shared_memory_mode,
        },
        "knowledge": {
            "state": knowledge_state,
            "label": knowledge_label,
            "summary": knowledge_summary,
            "detail": knowledge_detail,
            "enabled": knowledge_enabled,
            "available": knowledge_enabled and source_count > 0,
            "source_count": source_count,
        },
    }


def install_management_feature_status() -> None:
    """Attach authoritative feature status to optimized management responses."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import management_loading_performance, management_ui
    from .knowledge import get_loaded_knowledge

    original_snapshot = management_loading_performance._agent_snapshot

    @wraps(original_snapshot)
    def agent_snapshot_with_feature_status(
        hass: Any, entry: Any, subentry: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        snapshot = original_snapshot(hass, entry, subentry, *args, **kwargs)
        options = kwargs.get("config")
        if not isinstance(options, Mapping):
            options = subentry.data
        source_count = int(snapshot.get("knowledge_source_count", 0))
        if source_count == 0:
            loaded = get_loaded_knowledge(hass, entry.entry_id, subentry.subentry_id)
            if loaded is not None:
                source_count = int(loaded.source_count)
                snapshot["knowledge_source_count"] = source_count
        snapshot["feature_status"] = management_feature_status(
            options, knowledge_source_count=source_count
        )
        return snapshot

    management_loading_performance._agent_snapshot = agent_snapshot_with_feature_status

    original_command = management_ui.async_management_command

    @wraps(original_command)
    async def management_command_with_feature_status(
        hass: Any, user_id: str, is_admin: bool, message: dict[str, Any]
    ) -> dict[str, Any]:
        result = await original_command(hass, user_id, is_admin, message)
        section = message.get("section")
        action = message.get("action")
        if action != "list" or section not in {"memories", "knowledge"}:
            return result

        _entry, subentry = management_ui.entry_and_agent(
            hass, message.get("entry_id"), message.get("subentry_id")
        )
        enriched = dict(result)
        if section == "memories":
            enriched["feature_status"] = management_feature_status(
                subentry.data, knowledge_source_count=0
            )["memory"]
            return enriched

        sources = result.get("sources", [])
        source_count = len(sources) if isinstance(sources, list) else 0
        stats = result.get("stats")
        if isinstance(stats, Mapping):
            try:
                source_count = int(stats.get("source_count", source_count))
            except (TypeError, ValueError):
                pass
        enriched["feature_status"] = management_feature_status(
            subentry.data, knowledge_source_count=source_count
        )["knowledge"]
        return enriched

    management_ui.async_management_command = management_command_with_feature_status
    management_ui.MANAGEMENT_FRONTEND_MODULES = tuple(
        dict.fromkeys((*management_ui.MANAGEMENT_FRONTEND_MODULES, _FRONTEND_MODULE))
    )
