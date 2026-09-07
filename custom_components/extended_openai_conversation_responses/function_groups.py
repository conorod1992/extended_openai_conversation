"""Conversation-scoped on-demand loading for configured function tools."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
from .skill_availability import function_group_enabled

_RUNTIMES = "extended_openai_conversation_responses.function_group_runtimes"
type ToolAvailabilityPredicate = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class ToolRuntimeAvailability:
    """Stable primitive runtime support used by configured-tool assembly."""

    function_tools_supported: bool = True
    group_loader_supported: bool = True
    tool_available: ToolAvailabilityPredicate | None = None


_ACTIVE_TOOL_RUNTIME: ContextVar[ToolRuntimeAvailability | None] = ContextVar(
    "extended_openai_function_tool_runtime", default=None
)


@contextmanager
def function_tool_runtime_scope(
    *,
    function_tools_supported: bool = True,
    group_loader_supported: bool = True,
    tool_available: ToolAvailabilityPredicate | None = None,
) -> Iterator[None]:
    """Apply one request/Preview availability snapshot without global mutation."""
    token = _ACTIVE_TOOL_RUNTIME.set(
        ToolRuntimeAvailability(
            function_tools_supported=function_tools_supported,
            group_loader_supported=group_loader_supported,
            tool_available=tool_available,
        )
    )
    try:
        yield
    finally:
        _ACTIVE_TOOL_RUNTIME.reset(token)


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


def function_tool_runtime_available(tool: dict[str, Any]) -> bool:
    """Return whether a configured tool is individually executable."""
    if not function_tool_enabled(tool):
        return False
    function_config = tool.get("function", {})
    if function_config.get("type") == "bash":
        return function_config.get("allow_unsafe_shell") is True
    return True


def _effective_runtime(
    function_tools_supported: bool,
    group_loader_supported: bool,
    tool_available: ToolAvailabilityPredicate | None,
) -> ToolRuntimeAvailability:
    """Combine explicit caller constraints with the active request snapshot."""
    active = _ACTIVE_TOOL_RUNTIME.get()
    if active is None:
        return ToolRuntimeAvailability(
            function_tools_supported=function_tools_supported,
            group_loader_supported=group_loader_supported,
            tool_available=tool_available,
        )

    active_predicate = active.tool_available
    if tool_available is None:
        combined = active_predicate
    elif active_predicate is None:
        combined = tool_available
    else:

        def combined_predicate(tool: dict[str, Any]) -> bool:
            return tool_available(tool) and active_predicate(tool)

        combined = combined_predicate
    return ToolRuntimeAvailability(
        function_tools_supported=(
            function_tools_supported and active.function_tools_supported
        ),
        group_loader_supported=(
            group_loader_supported and active.group_loader_supported
        ),
        tool_available=combined,
    )


def _tool_is_available(
    tool: dict[str, Any],
    *,
    function_tools_supported: bool,
    tool_available: ToolAvailabilityPredicate | None,
) -> bool:
    """Resolve primitive per-tool availability without consulting group state."""
    return bool(
        function_tools_supported
        and function_tool_runtime_available(tool)
        and (tool_available is None or tool_available(tool))
    )


def _available_group_sets(
    configured_tools: list[dict[str, Any]] | None,
    groups: list[dict[str, Any]],
    *,
    function_tools_supported: bool,
    group_loader_supported: bool,
    tool_available: ToolAvailabilityPredicate | None,
) -> tuple[set[str], dict[str, dict[str, Any]], set[str]]:
    """Resolve executable members plus reachable on-demand and always groups."""
    runtime = _effective_runtime(
        function_tools_supported, group_loader_supported, tool_available
    )
    if configured_tools is None:
        available_names = (
            {name for group in groups for name in group.get("functions", [])}
            if runtime.function_tools_supported
            else set()
        )
    else:
        available_names = {
            tool["spec"]["name"]
            for tool in configured_tools
            if _tool_is_available(
                tool,
                function_tools_supported=runtime.function_tools_supported,
                tool_available=runtime.tool_available,
            )
        }

    always = {
        group["id"]
        for group in groups
        if function_group_enabled(group)
        and group["loading_mode"] == FUNCTION_GROUP_LOADING_ALWAYS
        and any(name in available_names for name in group["functions"])
    }
    on_demand = {
        group["id"]: group
        for group in groups
        if runtime.group_loader_supported
        and function_group_enabled(group)
        and group["loading_mode"] == FUNCTION_GROUP_LOADING_ON_DEMAND
        and any(name in available_names for name in group["functions"])
    }
    return available_names, on_demand, always


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
    *,
    function_tools_supported: bool = True,
    group_loader_supported: bool = True,
    tool_available: ToolAvailabilityPredicate | None = None,
) -> FunctionToolAssembly:
    """Centralize the effective configured tool set for one provider request."""
    membership = {
        function_name: group for group in groups for function_name in group["functions"]
    }
    available_names, on_demand, _always = _available_group_sets(
        configured_tools,
        groups,
        function_tools_supported=function_tools_supported,
        group_loader_supported=group_loader_supported,
        tool_available=tool_available,
    )

    # Never resurrect an old loaded state after the group becomes unavailable.
    loaded_group_ids.intersection_update(on_demand)

    effective: list[dict[str, Any]] = []
    for tool in configured_tools:
        tool_name = tool["spec"]["name"]
        if tool_name not in available_names:
            continue
        group = membership.get(tool_name)
        if group is not None and not function_group_enabled(group):
            continue
        if (
            group is None
            or group["loading_mode"] == FUNCTION_GROUP_LOADING_ALWAYS
            or group["id"] in loaded_group_ids
        ):
            effective.append(tool)

    unloaded = [
        group
        for group in groups
        if group["id"] in on_demand and group["id"] not in loaded_group_ids
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
    *,
    function_tools_supported: bool = True,
    group_loader_supported: bool = True,
    tool_available: ToolAvailabilityPredicate | None = None,
) -> dict[str, Any]:
    """Validate and apply one model-requested group load operation."""
    _available_names, on_demand, always = _available_group_sets(
        configured_tools,
        groups,
        function_tools_supported=function_tools_supported,
        group_loader_supported=group_loader_supported,
        tool_available=tool_available,
    )
    session.loaded_group_ids.intersection_update(on_demand)
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
