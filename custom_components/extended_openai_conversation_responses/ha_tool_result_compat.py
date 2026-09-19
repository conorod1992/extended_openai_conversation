"""Compatibility helpers for Home Assistant LLM tool results."""

from __future__ import annotations

from inspect import signature
from typing import Any, cast

from homeassistant.components import conversation
from homeassistant.helpers import llm


def make_tool_result_content(
    *, agent_id: str, tool_call_id: str, tool_name: str, tool_result: dict[str, Any]
) -> conversation.ToolResultContent:
    """Build ToolResultContent across old and new Home Assistant APIs."""
    kwargs: dict[str, Any] = {
        "agent_id": agent_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
    }
    parameters = signature(conversation.ToolResultContent).parameters
    if "result" in parameters:
        tool_result_type = cast(Any, llm).ToolResult
        kwargs["result"] = tool_result_type(data=tool_result)
    else:
        kwargs["tool_result"] = tool_result
    return conversation.ToolResultContent(**kwargs)


def unwrap_tool_result(value: Any) -> Any:
    """Return legacy raw tool-result data when HA wraps it in llm.ToolResult."""
    tool_result_type = getattr(llm, "ToolResult", None)
    if tool_result_type is not None and isinstance(value, tool_result_type):
        return value.data
    return value


def tool_result_data(content: Any) -> Any:
    """Return raw ToolResultContent data without touching HA's deprecated property."""
    missing = object()
    result = getattr(content, "result", missing)
    if result is not missing:
        return unwrap_tool_result(result)
    return getattr(content, "tool_result", None)
