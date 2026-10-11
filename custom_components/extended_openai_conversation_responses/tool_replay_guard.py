"""Prevent a lost provider continuation from replaying completed side effects."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
import json
from typing import Any

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .function_execution import _schema_without_patterns, validate_function_arguments
from .ha_tool_result_compat import is_tool_result_content
from .parallel_tool_execution import is_parallel_safe_integration_tool

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


def record_dispatch(
    entity: Any,
    tool_input: llm.ToolInput,
    function_tool: Mapping[str, Any] | None = None,
) -> None:
    """Keep trusted dispatch provenance separately from model-facing outcomes."""
    dispatched = getattr(entity, "_dispatched_tool_inputs", None)
    if not isinstance(dispatched, dict):
        dispatched = entity._dispatched_tool_inputs = {}
    read_only = getattr(entity, "_read_only_tool_inputs", None)
    if not isinstance(read_only, dict):
        read_only = entity._read_only_tool_inputs = {}
    verified_read = (
        function_tool is not None
        and is_parallel_safe_integration_tool(function_tool)
        # Scheduling even a read creates a durable side effect.
        and "delay" not in tool_input.tool_args
    )
    retained = [tool_input]
    origin = _dispatch_origin.get()
    if origin is not None and origin[0] is tool_input:
        retained.append(origin[1])
    canonical = getattr(entity, "_dispatched_tool_signatures", None)
    if not isinstance(canonical, dict):
        canonical = entity._dispatched_tool_signatures = {}
    spec = deepcopy(function_tool.get("spec", {})) if function_tool is not None else {}
    # Regex validation already belongs to the bounded execution validator. Only
    # repeat its context-free canonicalization here, never configured regex work.
    spec["parameters"] = _schema_without_patterns(spec.get("parameters", {}))
    signature = (
        _signature(tool_input, spec)
        if isinstance(getattr(tool_input, "tool_name", None), str)
        and isinstance(getattr(tool_input, "tool_args", None), Mapping)
        else None
    )
    for item in retained:
        dispatched[id(item)] = item
        if signature is not None:
            canonical[id(item)] = (signature, spec)
        if verified_read:
            read_only[id(item)] = item
        else:
            read_only.pop(id(item), None)
    while len(dispatched) > 4096:
        canonical.pop(next(iter(dispatched)), None)
        read_only.pop(next(iter(dispatched)), None)
        dispatched.pop(next(iter(dispatched)))


def _signature(tool_input: llm.ToolInput, spec: Mapping[str, Any] | None = None) -> str:
    arguments = tool_input.tool_args
    if spec is not None:
        # Invalid/externally owned arguments retain exact replay checks.
        with suppress(HomeAssistantError, TypeError, ValueError):
            arguments = validate_function_arguments(spec, arguments)
    return json.dumps(
        [tool_input.tool_name, arguments],
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
    }
    records = [
        getattr(entity, "_dispatched_tool_signatures", {}).get(
            id(tool_input), (_signature(tool_input), {})
        )
        for content in new_content
        if isinstance(content, conversation.AssistantContent) and content.tool_calls
        for tool_input in content.tool_calls
        if tool_input.id in completed_ids
        and getattr(entity, "_dispatched_tool_inputs", {}).get(id(tool_input))
        is tool_input
        and getattr(entity, "_read_only_tool_inputs", {}).get(id(tool_input))
        is not tool_input
    ]
    signatures = {signature for signature, _spec in records}
    if not signatures:
        return
    ledger = getattr(entity, "_unacknowledged_tool_calls", None)
    if not isinstance(ledger, dict):
        ledger = {}
        entity._unacknowledged_tool_calls = ledger
    ledger.setdefault(conversation_id, set()).update(signatures)
    schemas = getattr(entity, "_unacknowledged_tool_schemas", None)
    if not isinstance(schemas, dict):
        schemas = entity._unacknowledged_tool_schemas = {}
    schemas.setdefault(conversation_id, {}).update(dict(records))
    while len(ledger) > _MAX_AMBIGUOUS_CONVERSATIONS:
        schemas.pop(next(iter(ledger)), None)
        ledger.pop(next(iter(ledger)))


def was_unacknowledged_equivalent(
    entity: Any, chat_log: conversation.ChatLog, tool_input: llm.ToolInput
) -> bool:
    """Match only calls from an unresolved failed continuation in this conversation."""
    ledger = getattr(entity, "_unacknowledged_tool_calls", None)
    if not isinstance(ledger, dict):
        return False
    signatures = ledger.get(chat_log.conversation_id, ())
    if _signature(tool_input) in signatures:
        return True
    schemas = getattr(entity, "_unacknowledged_tool_schemas", {})
    return any(
        signature in signatures and _signature(tool_input, spec) == signature
        for signature, spec in schemas.get(chat_log.conversation_id, {}).items()
    )


def clear_unacknowledged_calls(entity: Any, conversation_id: str | None) -> None:
    """A successful provider turn resolves ambiguity for this conversation."""
    ledger = getattr(entity, "_unacknowledged_tool_calls", None)
    if isinstance(ledger, dict) and isinstance(conversation_id, str):
        ledger.pop(conversation_id, None)
        getattr(entity, "_unacknowledged_tool_schemas", {}).pop(conversation_id, None)
