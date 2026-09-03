"""Lossless compaction helpers for integration-owned model tool results."""

from __future__ import annotations

import json
from typing import Any

from .memory import MemoryRecord, memory_as_dict


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
    return {
        key: value
        for key, value in memory_as_dict(
            memory,
            include_scope=include_scope,
            personal_scope_id=personal_scope_id,
        ).items()
        if value is not None
    }


def omit_null_paging_cursor(result: dict[str, Any], cursor_key: str) -> dict[str, Any]:
    """Omit a paging cursor only when the backend explicitly says there is no page."""
    if result.get("has_more") is False and result.get(cursor_key) is None:
        return {key: value for key, value in result.items() if key != cursor_key}
    return result


def knowledge_search_payload(
    results: list[dict[str, Any]],
    *,
    applied_source_ids: list[str],
    ignored_source_ids: list[str],
    fell_back_to_all_sources: bool,
    filter_requested: bool,
    policy_filter_applied: bool,
) -> dict[str, Any]:
    """Return search results without an all-default source-filter envelope."""
    payload: dict[str, Any] = {"results": results}
    if filter_requested or policy_filter_applied:
        payload["source_filter"] = {
            "applied_source_ids": applied_source_ids,
            "ignored_source_ids": ignored_source_ids,
            "fell_back_to_all_sources": fell_back_to_all_sources,
        }
    return payload
