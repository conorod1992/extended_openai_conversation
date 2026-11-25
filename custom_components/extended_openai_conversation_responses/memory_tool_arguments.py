"""Optional arguments shared by integration-owned memory tools."""

from typing import Any


def nullable_memory_tool_arguments(
    tools: list[dict[str, Any]], optional: dict[str, frozenset[str]]
) -> list[dict[str, Any]]:
    """Allow a null placeholder only for the declared optional fields."""
    for tool in tools:
        properties = tool["spec"]["parameters"]["properties"]
        for name in optional.get(tool["function"]["operation"], ()):
            field = properties[name]
            field["type"] = [field["type"], "null"]
            if "enum" in field:
                field["enum"].append(None)
    return tools


def normalise_memory_tool_arguments(
    arguments: dict[str, Any], optional: frozenset[str]
) -> dict[str, Any]:
    """Copy tool arguments and omit only declared null placeholders."""
    return {
        name: value
        for name, value in arguments.items()
        if value is not None or name not in optional
    }
