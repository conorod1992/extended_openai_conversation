"""Compatibility helpers for Home Assistant LLM tool results."""

from __future__ import annotations

from inspect import signature
from typing import Any, cast

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm


def make_tool_result_content(
    *,
    agent_id: str,
    tool_call_id: str,
    tool_name: str,
    tool_result: dict[str, Any],
    error: bool = False,
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
        kwargs["result"] = tool_result_type(data=tool_result, error=error)
    else:
        kwargs["tool_result"] = tool_result
    return conversation.ToolResultContent(**kwargs)


class HAToolResultError(HomeAssistantError):
    """An upstream failure, with data retained for the tool's result boundary."""

    def __init__(self, data: Any) -> None:
        super().__init__("HA tool reported an execution failure")
        self.data = data


def execution_tool_result(value: Any) -> Any:
    """Translate failure metadata only at dispatch, before unwrapping data."""
    tool_result_type = getattr(llm, "ToolResult", None)
    if tool_result_type is not None and isinstance(value, tool_result_type):
        if getattr(value, "error", False):
            raise HAToolResultError(value.data)
        return value.data
    return value


def unwrap_tool_result(value: Any) -> Any:
    """Return legacy raw tool-result data when HA wraps it in llm.ToolResult."""
    tool_result_type = getattr(llm, "ToolResult", None)
    if tool_result_type is not None and isinstance(value, tool_result_type):
        return value.data
    return value


_MISSING_RESULT = object()


def is_tool_result_content(content: Any) -> bool:
    """Recognize tool-result content without relying on runtime class identity.

    Supported HA versions expose either ``result`` or legacy ``tool_result``.
    Test doubles use the same stable call-id/payload contract.
    """
    if not isinstance(getattr(content, "tool_call_id", None), str):
        return False
    return hasattr(content, "result") or hasattr(content, "tool_result")


def tool_result_data(content: Any, default: Any = None) -> Any:
    """Read a chat-log result without accessing HA's deprecated compatibility property.

    The fallback must be lazy: newer HA still exposes ``tool_result``, but reading
    it reports deprecated usage. Return the original data, not a copy, so existing
    result projections can continue to compact their owned payload in place.
    """
    result = getattr(content, "result", _MISSING_RESULT)
    if result is not _MISSING_RESULT:
        return unwrap_tool_result(result)
    return getattr(content, "tool_result", default)
