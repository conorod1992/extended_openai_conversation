"""Lossless compaction for integration-owned model tool results."""

from __future__ import annotations

import json
from typing import Any, cast

from .memory import MemoryRecord, memory_as_dict

_INSTALLED = False
_OPTIONAL_MEMORY_FIELDS = {
    "subject",
    "key",
    "valid_from",
    "last_confirmed_at",
}


def compact_tool_json(value: Any) -> str:
    """Serialize model-facing tool data without semantically irrelevant whitespace."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def model_memory_as_dict(
    memory: MemoryRecord,
    *,
    include_scope: bool = False,
    personal_scope_id: str | None = None,
) -> dict[str, Any]:
    """Project a memory for the model while omitting only absent optional fields."""
    return _sparse_memory_mapping(
        memory_as_dict(
            memory,
            include_scope=include_scope,
            personal_scope_id=personal_scope_id,
        )
    )


def _sparse_memory_mapping(value: dict[str, Any]) -> dict[str, Any]:
    """Remove only optional memory fields whose canonical value is absent."""
    return {
        key: item
        for key, item in value.items()
        if key not in _OPTIONAL_MEMORY_FIELDS or item is not None
    }


def _compact_memory_result(result: dict[str, Any]) -> dict[str, Any]:
    """Sparsify only recognized memory-record containers."""
    compacted = dict(result)
    for key in ("memory", "candidate"):
        record = compacted.get(key)
        if isinstance(record, dict):
            compacted[key] = _sparse_memory_mapping(record)
    records = compacted.get("memories")
    if isinstance(records, list):
        compacted["memories"] = [
            _sparse_memory_mapping(record) if isinstance(record, dict) else record
            for record in records
        ]
    return compacted


def omit_null_paging_cursor(result: dict[str, Any], cursor_key: str) -> dict[str, Any]:
    """Omit a paging cursor only when the backend explicitly says there is no page."""
    if result.get("has_more") is False and result.get(cursor_key) is None:
        return {key: value for key, value in result.items() if key != cursor_key}
    return result


def knowledge_search_payload(
    result: dict[str, Any],
    *,
    filter_requested: bool,
    policy_filter_applied: bool,
) -> dict[str, Any]:
    """Omit only an all-default source-filter envelope that represents no filtering."""
    if filter_requested or policy_filter_applied:
        return result
    source_filter = result.get("source_filter")
    if source_filter == {
        "applied_source_ids": [],
        "ignored_source_ids": [],
        "fell_back_to_all_sources": False,
    }:
        return {key: value for key, value in result.items() if key != "source_filter"}
    return result


def _compact_json_result_content(result: Any) -> Any:
    """Compact a ToolResultContent JSON string while preserving parsed semantics."""
    tool_result = getattr(result, "tool_result", None)
    if not isinstance(tool_result, dict):
        return result
    value = tool_result.get("result")
    if not isinstance(value, str):
        return result
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return result
    compacted = compact_tool_json(parsed)
    if compacted == value:
        return result
    tool_result["result"] = compacted
    return result


def install_model_tool_result_compaction() -> None:
    """Install model-only result projections without changing backend/admin shapes."""
    global _INSTALLED
    if _INSTALLED:
        return

    from .conversation import ExtendedOpenAIAgentEntity

    agent_type: Any = ExtendedOpenAIAgentEntity
    original_execute = agent_type._execute_function_tool
    original_knowledge = agent_type._async_execute_knowledge_tool
    original_memory = agent_type._async_execute_memory_tool

    async def execute_function_tool(
        agent: Any, function_tool: Any, tool_input: Any, llm_context: Any, entities: Any
    ) -> Any:
        result = await original_execute(
            agent, function_tool, tool_input, llm_context, entities
        )
        return _compact_json_result_content(result)

    async def execute_knowledge(
        agent: Any, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        result = cast(
            dict[str, Any], await original_knowledge(agent, operation, arguments)
        )
        if operation == "search":
            policy_ids = agent._effective_guest_policy().knowledge_source_ids
            return knowledge_search_payload(
                result,
                filter_requested=bool(arguments.get("source_ids")),
                policy_filter_applied=policy_ids is not None,
            )
        if operation == "list":
            return omit_null_paging_cursor(result, "next_offset")
        if operation == "get":
            return omit_null_paging_cursor(result, "next_start_character")
        return result

    async def execute_memory(
        agent: Any,
        operation: str,
        arguments: dict[str, Any],
        llm_context: Any,
    ) -> dict[str, Any]:
        result = cast(
            dict[str, Any],
            await original_memory(agent, operation, arguments, llm_context),
        )
        return _compact_memory_result(result)

    agent_type._execute_function_tool = execute_function_tool
    agent_type._async_execute_knowledge_tool = execute_knowledge
    agent_type._async_execute_memory_tool = execute_memory
    _INSTALLED = True
