"""Static classification of integration-owned Function Tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from .built_in_functions import BUILT_IN_FUNCTION_PRESETS
from .const import FUNCTION_GROUP_LOADER_TOOL_NAME

FUNCTION_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CONTINUE_CONVERSATION_TOOL_NAME = "set_continue_conversation"


@dataclass(frozen=True, slots=True)
class _RuntimeFunctionTool:
    name: str
    read_only: bool = False


# Runtime-generated tools use their type and operation as their identity. The
# group loader has no operation. Provider-only finalization is reserved below.
_RUNTIME_FUNCTION_TOOLS: dict[str, dict[str | None, _RuntimeFunctionTool]] = {
    "archive": {
        "search": _RuntimeFunctionTool("conversation_search", read_only=True),
        "get": _RuntimeFunctionTool("conversation_get", read_only=True),
        "private": _RuntimeFunctionTool("conversation_private"),
        "resume": _RuntimeFunctionTool("conversation_resume_saving"),
        "delete_current": _RuntimeFunctionTool("conversation_delete_current"),
        "delete_selected": _RuntimeFunctionTool("conversation_delete_selected"),
        "delete_range": _RuntimeFunctionTool("conversation_delete_date_range"),
    },
    "conversation_lifecycle": {
        "start_fresh": _RuntimeFunctionTool("start_fresh_conversation"),
    },
    "function_group_loader": {
        None: _RuntimeFunctionTool(FUNCTION_GROUP_LOADER_TOOL_NAME),
    },
    "guest_mode": {"restrict": _RuntimeFunctionTool("guest_mode_restrict")},
    "knowledge": {
        "search": _RuntimeFunctionTool("knowledge_search", read_only=True),
        "list": _RuntimeFunctionTool("knowledge_list", read_only=True),
        "get": _RuntimeFunctionTool("knowledge_get", read_only=True),
    },
    "memory": {
        "add": _RuntimeFunctionTool("memory_add"),
        "upsert": _RuntimeFunctionTool("memory_upsert"),
        "search": _RuntimeFunctionTool("memory_search", read_only=True),
        "list": _RuntimeFunctionTool("memory_list", read_only=True),
        "update": _RuntimeFunctionTool("memory_update"),
        "delete": _RuntimeFunctionTool("memory_delete"),
    },
    "scheduled_actions": {
        "list": _RuntimeFunctionTool("list_scheduled_actions", read_only=True),
        "cancel": _RuntimeFunctionTool("cancel_scheduled_action"),
    },
    "temporary_memory": {
        "add": _RuntimeFunctionTool("temporary_memory_add"),
        "update": _RuntimeFunctionTool("temporary_memory_update"),
        "delete": _RuntimeFunctionTool("temporary_memory_delete"),
    },
}

RUNTIME_FUNCTION_TOOL_TYPES = frozenset(_RUNTIME_FUNCTION_TOOLS)

# Reserve generated names even while their capabilities are disabled.
RESERVED_FUNCTION_TOOL_NAMES = frozenset(
    {
        CONTINUE_CONVERSATION_TOOL_NAME,
        *(
            tool.name
            for operations in _RUNTIME_FUNCTION_TOOLS.values()
            for tool in operations.values()
        ),
    }
)

# Keep native validation tied to the same catalogue that populates the management UI.
NATIVE_FUNCTION_IMPLEMENTATIONS = frozenset(
    preset["implementation"] for preset in BUILT_IN_FUNCTION_PRESETS
)

# Configured native tools can use custom names. Trust these implementations by
# their internal name and leave runtime ownership with the configured catalogue.
_READ_ONLY_NATIVE_IMPLEMENTATIONS = frozenset(
    {"get_history", "get_energy", "get_statistics", "get_user_from_user_id"}
)


def runtime_function_tool_name(function_type: str, operation: str) -> str:
    """Return the reserved provider name for a generated tool."""
    return _RUNTIME_FUNCTION_TOOLS[function_type][operation].name


def runtime_function_tool_names(function_type: str) -> frozenset[str]:
    """Return the reserved provider names for one generated tool family."""
    return frozenset(
        tool.name for tool in _RUNTIME_FUNCTION_TOOLS[function_type].values()
    )


def is_integration_owned_read_only_tool(tool: Mapping[str, Any]) -> bool:
    """Recognize implementations trusted by effect replay protection."""
    function = tool.get("function")
    if not isinstance(function, Mapping):
        return False
    function_type = function.get("type", "")
    if function_type == "native":
        return function.get("name") in _READ_ONLY_NATIVE_IMPLEMENTATIONS
    operations = _RUNTIME_FUNCTION_TOOLS.get(function_type, {})
    definition = operations.get(function.get("operation"))
    return definition is not None and definition.read_only
