"""Protocol-safe retained Function Tool exchange helpers."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components import conversation
from homeassistant.helpers import llm

_MAX_ERROR_TEXT = 512


def _error_text(error: BaseException | None) -> str:
    """Return a bounded model-visible description for a failed exchange."""
    if error is None:
        return "Tool execution was interrupted before completion"
    name = type(error).__name__
    detail = str(error).strip()
    text = f"{name}: {detail}" if detail else name
    if len(text) <= _MAX_ERROR_TEXT:
        return text
    return f"{text[: _MAX_ERROR_TEXT - 1]}…"


def retained_tool_calls_since(
    chat_log: conversation.ChatLog, existing_content_ids: set[int]
) -> list[llm.ToolInput]:
    """Return external tool calls actually retained during one provider round."""
    calls: list[llm.ToolInput] = []
    for content in chat_log.content:
        if id(content) in existing_content_ids:
            continue
        if isinstance(content, conversation.AssistantContent) and content.tool_calls:
            calls.extend(content.tool_calls)
    return calls


def append_unresolved_tool_results(
    chat_log: conversation.ChatLog,
    agent_id: str,
    tool_calls: Iterable[llm.ToolInput],
    *,
    failed_call_id: str | None = None,
    error: BaseException | None = None,
) -> None:
    """Close every still-retained call exactly once before an error escapes.

    Results already recorded by successful calls are preserved. The call that caused
    the abort is represented as an error; other calls that never reached a completed
    result are explicitly marked skipped. If the failure is at provider/round level
    rather than attributable to one call, the first unresolved call carries the error
    and the remaining calls are skipped.
    """
    calls = list(tool_calls)
    if not calls:
        return

    retained_ids = {
        tool_call.id
        for content in chat_log.content
        if isinstance(content, conversation.AssistantContent) and content.tool_calls
        for tool_call in content.tool_calls
    }
    completed_ids = {
        content.tool_call_id
        for content in chat_log.content
        if isinstance(content, conversation.ToolResultContent)
    }
    unresolved = [
        tool_call
        for tool_call in calls
        if tool_call.id in retained_ids and tool_call.id not in completed_ids
    ]
    if not unresolved:
        return

    actual_failed_id = (
        failed_call_id
        if failed_call_id is not None
        and any(call.id == failed_call_id for call in unresolved)
        else unresolved[0].id
    )
    failure_text = _error_text(error)
    failed_name = next(
        (call.tool_name for call in unresolved if call.id == actual_failed_id),
        "another tool call",
    )

    for tool_call in unresolved:
        if tool_call.id == actual_failed_id:
            result = {"status": "error", "error": failure_text}
        else:
            result = {
                "status": "skipped",
                "error": (
                    f"Skipped because tool call `{failed_name}` failed before this "
                    "exchange completed"
                ),
            }
        chat_log.async_add_assistant_content_without_tools(
            conversation.ToolResultContent(
                agent_id=agent_id,
                tool_call_id=tool_call.id,
                tool_name=tool_call.tool_name,
                tool_result={"result": result},
            )
        )
