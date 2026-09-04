"""Execution-time resolution for configured Function Tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# These definitions are owned by the integration runtime rather than the user's
# persisted Function Tool catalogue. Their request-round objects are authoritative.
_INTEGRATION_TOOL_TYPES = frozenset(
    {
        "guest_mode",
        "memory",
        "temporary_memory",
        "knowledge",
        "archive",
        "function_group_loader",
    }
)


def latest_function_tool_for_execution(
    agent: Any,
    function_tool: dict[str, Any],
) -> dict[str, Any]:
    """Return the newest persisted definition immediately before execution.

    Configured Function Tools may be edited, disabled, or deleted after a provider
    request has already been emitted. When the current definition still exists,
    execute that exact object. If it was deleted, retain the request-round object so
    the conversation executor's existing authoritative lookup fails closed.
    """
    function = function_tool.get("function")
    if (
        not isinstance(function, Mapping)
        or function.get("type") in _INTEGRATION_TOOL_TYPES
    ):
        return function_tool

    tool_name = function_tool.get("spec", {}).get("name")
    if not isinstance(tool_name, str) or not tool_name:
        return function_tool

    resolver = getattr(agent, "_configured_function_tools_from_data", None)
    if not callable(resolver):
        return function_tool

    latest_entry = agent.hass.config_entries.async_get_entry(agent.entry.entry_id)
    latest_subentry = (
        latest_entry.subentries.get(agent.subentry.subentry_id)
        if latest_entry is not None
        else None
    )
    latest_data = (
        latest_subentry.data if latest_subentry is not None else agent.subentry.data
    )
    current_tools = resolver(latest_data)
    current_tool = next(
        (
            candidate
            for candidate in current_tools
            if candidate.get("spec", {}).get("name") == tool_name
        ),
        None,
    )
    return current_tool if current_tool is not None else function_tool
