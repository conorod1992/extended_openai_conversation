"""Prevent a lost provider continuation from replaying completed side effects."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import llm

from .ha_tool_result_compat import is_tool_result_content, tool_result_data

_MAX_AMBIGUOUS_CONVERSATIONS = 32
_dispatch_origin: ContextVar[tuple[llm.ToolInput, llm.ToolInput] | None] = ContextVar(
    "tool_dispatch_origin", default=None
)


@contextmanager
def bind_dispatch_origin(
    dispatched: llm.ToolInput, retained: llm.ToolInput
) -> Iterator[None]:
    """Link a validated copy to its retained call only during this execution."""
    token = _dispatch_origin.set((dispatched, retained))
    try:
        yield
    finally:
        _dispatch_origin.reset(token)


def record_dispatch(entity: Any, tool_input: llm.ToolInput) -> None:
    """Keep trusted dispatch provenance separately from model-facing outcomes."""
    dispatched = getattr(entity, "_dispatched_tool_inputs", None)
    if not isinstance(dispatched, dict):
        dispatched = entity._dispatched_tool_inputs = {}
    dispatched[id(tool_input)] = tool_input
    origin = _dispatch_origin.get()
    if origin is not None and origin[0] is tool_input:
        dispatched[id(origin[1])] = origin[1]
    while len(dispatched) > 4096:
        dispatched.pop(next(iter(dispatched)))


def _signature(tool_input: llm.ToolInput) -> str:
    return json.dumps(
        [tool_input.tool_name, tool_input.tool_args],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def remember_unacknowledged_calls(
    entity: Any,
    chat_log: conversation.ChatLog,
    existing_content_ids: set[int],
) -> None:
    """Remember completed calls if the provider failed before accepting results."""
    conversation_id = getattr(chat_log, "conversation_id", None)
    if not isinstance(conversation_id, str) or not conversation_id:
        return
    new_content = [
        content
        for content in chat_log.content
        if id(content) not in existing_content_ids
    ]
    completed_ids = {
        call_id
        for content in new_content
        if is_tool_result_content(content)
        and isinstance((call_id := getattr(content, "tool_call_id", None)), str)
        and not _was_skipped(content)
    }
    signatures = {
        _signature(tool_input)
        for content in new_content
        if isinstance(content, conversation.AssistantContent) and content.tool_calls
        for tool_input in content.tool_calls
        if tool_input.id in completed_ids
        and getattr(entity, "_dispatched_tool_inputs", {}).get(id(tool_input))
        is tool_input
    }
    if not signatures:
        return
    ledger = getattr(entity, "_unacknowledged_tool_calls", None)
    if not isinstance(ledger, dict):
        ledger = {}
        entity._unacknowledged_tool_calls = ledger
    ledger.setdefault(conversation_id, set()).update(signatures)
    while len(ledger) > _MAX_AMBIGUOUS_CONVERSATIONS:
        ledger.pop(next(iter(ledger)))


def _was_skipped(content: Any) -> bool:
    data = tool_result_data(content)
    result = data.get("result") if isinstance(data, dict) else None
    return isinstance(result, dict) and result.get("status") == "skipped"


def was_unacknowledged_equivalent(
    entity: Any, chat_log: conversation.ChatLog, tool_input: llm.ToolInput
) -> bool:
    """Match only calls from an unresolved failed continuation in this conversation."""
    ledger = getattr(entity, "_unacknowledged_tool_calls", None)
    if not isinstance(ledger, dict):
        return False
    return _signature(tool_input) in ledger.get(chat_log.conversation_id, ())


def clear_unacknowledged_calls(entity: Any, conversation_id: str | None) -> None:
    """A successful provider turn resolves ambiguity for this conversation."""
    ledger = getattr(entity, "_unacknowledged_tool_calls", None)
    if isinstance(ledger, dict) and isinstance(conversation_id, str):
        ledger.pop(conversation_id, None)
