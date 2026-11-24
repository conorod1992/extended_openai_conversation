"""Read-only Function Tool identities trusted by effect replay protection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_READ_ONLY_OPERATIONS = frozenset(
    {
        ("memory", "search"),
        ("memory", "list"),
        ("knowledge", "search"),
        ("knowledge", "list"),
        ("knowledge", "get"),
        ("archive", "search"),
        ("archive", "get"),
    }
)

# Identify native reads by their implementation rather than their exposed name.
_READ_ONLY_NATIVE_NAMES = frozenset(
    {
        "get_history",
        "get_energy",
        "get_statistics",
        "get_user_from_user_id",
    }
)


def is_integration_owned_read_only_tool(tool: Mapping[str, Any]) -> bool:
    """Recognize integration-owned reads from the active tool definition."""
    function = tool.get("function")
    if not isinstance(function, Mapping):
        return False
    function_type = function.get("type")
    if function_type == "native":
        return function.get("name") in _READ_ONLY_NATIVE_NAMES
    return (function_type, function.get("operation")) in _READ_ONLY_OPERATIONS
