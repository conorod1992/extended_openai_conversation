"""Conversation-scoped on-demand loading for configured function tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any, cast

from .agent_config import function_tool_enabled
from .const import (
    FUNCTION_GROUP_LOADER_TOOL_NAME,
    FUNCTION_GROUP_LOADING_ALWAYS,
    FUNCTION_GROUP_LOADING_ON_DEMAND,
)

_RUNTIMES = "extended_openai_conversation_responses.function_group_runtimes"


@dataclass(slots=True)
class FunctionGroupSession:
    """Ephemeral loaded-group state for one active logical conversation."""

    session_key: str
    last_active: float
    loaded_group_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class FunctionToolAssembly:
    """Effective configured tools and compact request observability."""

    tools: list[dict[str, Any]]
    configured_count: int
    configured_schemas_sent: int
    available_on_demand_groups: int
    loaded_group_ids: list[str]
    serialized_configured_schema_characters: int


class FunctionGroupRuntime:
    """Bounded in-memory state isolated to one config entry and agent."""

    def __init__(self) -> None:
        self._sessions: dict[str, FunctionGroupSession] = {}
        self._last_request: dict[str, Any] = {}

    def begin(self, session_key: str, timeout_minutes: int) -> FunctionGroupSession:
        """Resolve a session and prune inactive conversation state."""
        now = time.monotonic()
        cutoff = now - max(1, timeout_minutes) * 60
        for key, current_session in list(self._sessions.items()):
            if current_session.last_active < cutoff:
                del self._sessions[key]
        session = self._sessions.get(session_key)
        if session is None:
            session = FunctionGroupSession(session_key, now)
            self._sessions[session_key] = session
        else:
            session.last_active = now
        return session

    def record_request(self, assembly: FunctionToolAssembly) -> None:
        """Retain only non-sensitive schema counts for diagnostics."""
        self._last_request = {
            "configured_function_tools": assembly.configured_count,
            "configured_function_schemas_sent": assembly.configured_schemas_sent,
            "available_on_demand_groups": assembly.available_on_demand_groups,
            "loaded_function_groups": assembly.loaded_group_ids,
            "serialized_configured_function_schema_characters": (
                assembly.serialized_configured_schema_characters
            ),
        }

    def end(self, session_key: str) -> bool:
        """Discard loaded groups when one logical conversation ends."""
        return self._sessions.pop(session_key, None) is not None

    def stats(self) -> dict[str, Any]:
        """Return non-sensitive runtime diagnostics."""
        return {
            "active_function_group_sessions": len(self._sessions),
            **self._last_request,
        }


def reset_function_group_runtime(
    hass: Any, entry_id: str, subentry_id: str
) -> FunctionGroupRuntime:
    """Create fresh runtime state when an agent is loaded or reconfigured."""
    managers = hass.data.setdefault(_RUNTIMES, {})
    runtime = FunctionGroupRuntime()
    managers[(entry_id, subentry_id)] = runtime
    return runtime


def get_function_group_runtime(
    hass: Any, entry_id: str, subentry_id: str
) -> FunctionGroupRuntime | None:
    """Return existing runtime state without creating a diagnostics side effect."""
    return cast(
        FunctionGroupRuntime | None,
        hass.data.get(_RUNTIMES, {}).get((entry_id, subentry_id)),
    )


def remove_function_group_runtime(hass: Any, entry_id: str, subentry_id: str) -> None:
    """Discard all loaded groups when an agent unloads."""
    managers = hass.data.get(_RUNTIMES, {})
    managers.pop((entry_id, subentry_id), None)


def build_loader_tool(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the compact integration-owned group catalogue tool."""
    group_ids = [group["id"] for group in groups]
    catalogue = "\n".join(
        f"- {group['id']}: {group['name']} — {group['description']}" for group in groups
    )
    return {
        "spec": {
            "name": FUNCTION_GROUP_LOADER_TOOL_NAME,
            "description": (
                "Load detailed definitions for only the function groups relevant to "
                "the user's current task. You may load several groups in one call. "
                "Loading exposes configured tools but performs no user-visible action. "
                "Available groups:\n" + catalogue
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "groups": {
                        "type": "array",
                        "items": {"type": "string", "enum": group_ids},
                    }
                },
                "required": ["groups"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "function_group_loader"},
    }


def assemble_function_tools(
    configured_tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    loaded_group_ids: set[str],
) -> FunctionToolAssembly:
    """Centralize the effective configured tool set for one provider request."""
    groups_by_id = {group["id"]: group for group in groups}
    membership = {
        function_name: group for group in groups for function_name in group["functions"]
    }
    current_on_demand_ids = {
        group_id
        for group_id, group in groups_by_id.items()
        if group["loading_mode"] == FUNCTION_GROUP_LOADING_ON_DEMAND
    }
    loaded_group_ids.intersection_update(current_on_demand_ids)

    enabled_tools = [tool for tool in configured_tools if function_tool_enabled(tool)]
    enabled_names = {tool["spec"]["name"] for tool in enabled_tools}
    effective: list[dict[str, Any]] = []
    for tool in enabled_tools:
        group = membership.get(tool["spec"]["name"])
        if (
            group is None
            or group["loading_mode"] == FUNCTION_GROUP_LOADING_ALWAYS
            or group["id"] in loaded_group_ids
        ):
            effective.append(tool)

    unloaded = [
        group
        for group in groups
        if group["loading_mode"] == FUNCTION_GROUP_LOADING_ON_DEMAND
        and group["id"] not in loaded_group_ids
        and any(name in enabled_names for name in group["functions"])
    ]
    if unloaded:
        effective.append(build_loader_tool(unloaded))

    serialized_characters = sum(
        len(json.dumps(tool["spec"], ensure_ascii=False, separators=(",", ":")))
        for tool in effective
        if tool["spec"]["name"] != FUNCTION_GROUP_LOADER_TOOL_NAME
    )
    sent_configured = sum(
        tool["spec"]["name"] != FUNCTION_GROUP_LOADER_TOOL_NAME for tool in effective
    )
    return FunctionToolAssembly(
        tools=effective,
        configured_count=len(configured_tools),
        configured_schemas_sent=sent_configured,
        available_on_demand_groups=len(unloaded),
        loaded_group_ids=sorted(loaded_group_ids),
        serialized_configured_schema_characters=serialized_characters,
    )


def load_function_groups(
    session: FunctionGroupSession,
    requested: Any,
    groups: list[dict[str, Any]],
    configured_tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and apply one model-requested group load operation."""
    enabled_names = {
        tool["spec"]["name"]
        for tool in configured_tools or []
        if function_tool_enabled(tool)
    }
    on_demand = {
        group["id"]: group
        for group in groups
        if group["loading_mode"] == FUNCTION_GROUP_LOADING_ON_DEMAND
        and (
            configured_tools is None
            or any(name in enabled_names for name in group["functions"])
        )
    }
    always = {
        group["id"]
        for group in groups
        if group["loading_mode"] == FUNCTION_GROUP_LOADING_ALWAYS
    }
    if (
        not isinstance(requested, list)
        or not requested
        or not all(isinstance(item, str) for item in requested)
    ):
        return {
            "status": "error",
            "error": "groups must be a non-empty array of group IDs",
            "loadable_groups": sorted(
                group_id
                for group_id in on_demand
                if group_id not in session.loaded_group_ids
            ),
        }

    loaded: list[str] = []
    already_loaded: list[str] = []
    already_available: list[str] = []
    unknown: list[str] = []
    for group_id in dict.fromkeys(requested):
        if group_id in session.loaded_group_ids and group_id in on_demand:
            already_loaded.append(group_id)
        elif group_id in on_demand:
            session.loaded_group_ids.add(group_id)
            loaded.append(group_id)
        elif group_id in always:
            already_available.append(group_id)
        else:
            unknown.append(group_id)
    session.last_active = time.monotonic()
    return {
        "status": "success" if not unknown else "partial" if loaded else "error",
        "loaded": loaded,
        "already_loaded": already_loaded,
        "already_available": already_available,
        "unknown": unknown,
        "loadable_groups": sorted(
            group_id
            for group_id in on_demand
            if group_id not in session.loaded_group_ids
        ),
    }
