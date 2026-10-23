"""Lossless compaction for integration-owned model tool results."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
from typing import Any

import orjson

from .ha_tool_result_compat import (
    BackendExecutionFailure,
    ExecutionFailure,
    ToolResultPayload,
    is_tool_result_content,
    tool_result_data,
)
from .memory import MemoryRecord, memory_as_dict

_OPTIONAL_MEMORY_FIELDS = {
    "subject",
    "key",
    "valid_from",
}

_LOCAL_RESULTS: ContextVar[bool] = ContextVar(
    "eoai_local_function_results", default=False
)


@contextmanager
def local_function_results() -> Iterator[None]:
    """Keep local dataflow outside model presentation and its bounds."""
    token = _LOCAL_RESULTS.set(True)
    try:
        yield
    finally:
        _LOCAL_RESULTS.reset(token)


def local_results_requested() -> bool:
    return _LOCAL_RESULTS.get()


class OwnedJSONPayload(dict[str, Any]):
    """An explicitly integration-serialized envelope, never inferred from text."""

    def __init__(self, value: Any) -> None:
        super().__init__(result=compact_tool_json(value))
        self.value = value


def _overflow_result(*, error: bool = False) -> dict[str, Any]:
    overflow = {
        "status": "output_overflow",
        "execution_completed": True,
        "message": "The completed Function result exceeds the model output limit. Use a bounded query; do not repeat side effects.",
    }
    if error:
        overflow["error"] = True
        return ExecutionFailure(overflow)
    return overflow


def _tool_result_failed(content: Any, payload: Any) -> bool:
    """Retain native and structured failure indications without parsing text."""
    if isinstance(payload, OwnedJSONPayload):
        value = payload.value
    elif isinstance(payload, dict):
        value = payload.get("result", payload)
    else:
        value = payload
    if bool(getattr(getattr(content, "result", None), "error", False)) or (
        isinstance(payload, OwnedJSONPayload)
        and isinstance(value, dict)
        and (value.get("status") == "error" or value.get("error") is True)
    ):
        return True
    # Native batches can contain both successful results and handled failures.
    # Recognise only producer-owned markers at arbitrary nesting; a plain
    # business-data dictionary containing an "error" string remains ordinary data.
    pending = [payload, value]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, ExecutionFailure):
            return True
        if not isinstance(item, (dict, list, tuple)) or id(item) in seen:
            continue
        seen.add(id(item))
        pending.extend(item.values() if isinstance(item, dict) else item)
    return False


def _project_tool_payload(payload: Any, *, error: bool = False) -> Any:
    """Project the result value without mutating backend-owned data."""
    from .runtime_hardening import (
        MAX_MODEL_TOOL_RESULT_CHARACTERS,
        bounded_tool_result_text,
    )

    if not isinstance(payload, dict) or "result" not in payload:
        return payload
    owned = isinstance(payload, OwnedJSONPayload)
    value = (
        payload.value if isinstance(payload, OwnedJSONPayload) else payload["result"]
    )
    projected: Any
    if isinstance(value, str) and not owned:
        projected = bounded_tool_result_text(value)
    else:
        serialized = payload["result"] if owned else compact_tool_json(value)
        projected = (
            value
            if len(serialized) <= MAX_MODEL_TOOL_RESULT_CHARACTERS
            else _overflow_result(error=error)
        )
    # Never mutate the retained execution result or a backend-owned object.
    # The ownership marker is internal: HA's dataclasses.asdict reconstructs
    # dict subclasses with an iterator, incompatible with its value constructor.
    if owned or projected is not value:
        if owned:
            projected = compact_tool_json(_public_backend_failures(projected))
        return {**payload, "result": projected}
    return payload


async def project_tool_result(content: Any, *, prepare_wire: bool = False) -> Any:
    """Prepare failure provenance, bounded data and wire output off the event loop."""
    import asyncio

    payload = tool_result_data(content)
    if isinstance(payload, ToolResultPayload) and payload.model_wire is not None:
        return content
    retained, error, _, changed = await asyncio.to_thread(_prepare_tool_result, content)
    # Execution wrappers preserve unchanged backend results. Prepared log data
    # remains complete; only its internal wire cache contains the projection.
    if not prepare_wire and not changed:
        return content
    if isinstance(retained, dict):
        from .ha_tool_result_compat import make_tool_result_content

        return make_tool_result_content(
            agent_id=content.agent_id,
            tool_call_id=content.tool_call_id,
            tool_name=content.tool_name,
            tool_result=retained,
            error=error,
        )
    return content


async def async_prepare_tool_results(chat_content: list[Any]) -> None:
    """Prepare retained/new results once without changing backend/local objects."""
    snapshot = list(chat_content)
    prepared = {
        id(content): await project_tool_result(content, prepare_wire=True)
        for content in snapshot
        if is_tool_result_content(content)
    }
    chat_content[:] = [prepared.get(id(content), content) for content in chat_content]


def _prepare_tool_result(content: Any) -> tuple[Any, bool, str, bool]:
    """Perform all payload-sized work together on the worker thread."""
    from .runtime_hardening import MAX_MODEL_TOOL_RESULT_CHARACTERS

    payload = tool_result_data(content)
    retained = payload
    error = _tool_result_failed(content, payload)
    payload = _project_tool_payload(payload, error=error)
    # The established budget measures the result value before the provider's
    # outer JSON encoding. Counting its escaping again would discard complete
    # bounded knowledge pages and turn clipped literal text into structured data.
    if isinstance(payload, dict) and "result" in payload:
        value = payload["result"]
        size = len(value) if isinstance(value, str) else len(compact_tool_json(value))
        other_data = {key: value for key, value in payload.items() if key != "result"}
        # Sibling fields share the result's budget, including their JSON keys
        # and structure. Only the fixed result envelope/outer escaping is extra.
        if other_data:
            size += len(compact_tool_json(other_data))
        if size > MAX_MODEL_TOOL_RESULT_CHARACTERS:
            payload = {"result": _overflow_result(error=error)}
    elif len(compact_tool_json(payload)) > MAX_MODEL_TOOL_RESULT_CHARACTERS:
        payload = _overflow_result(error=error)
    payload = _public_backend_failures(payload)
    wire = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS).decode()
    changed = payload is not retained
    if isinstance(retained, dict):
        retained = ExecutionFailure(retained) if error else ToolResultPayload(retained)
        retained.model_wire = wire
    return retained, error, wire, changed


def _public_backend_failures(value: Any) -> Any:
    """Contain only producer-owned failure prose, never infer from business keys."""
    from .public_errors import SanitizedBackendError

    if isinstance(value, BackendExecutionFailure):
        return {"error": str(SanitizedBackendError())}
    if isinstance(value, dict):
        projected = {key: _public_backend_failures(item) for key, item in value.items()}
        # Explicit integration-owned JSON retains its parsed value for this
        # boundary. Its string representation must be rebuilt after projection.
        if isinstance(value, OwnedJSONPayload):
            return {"result": compact_tool_json(_public_backend_failures(value.value))}
        return (
            projected
            if any(projected[key] is not item for key, item in value.items())
            else value
        )
    if isinstance(value, (list, tuple)):
        items = [_public_backend_failures(item) for item in value]
        if all(projected is item for projected, item in zip(items, value, strict=True)):
            return value
        return tuple(items) if isinstance(value, tuple) else items
    return value


def serialize_model_tool_result(content: Any) -> str:
    """Reuse prepared output; standalone conversion keeps synchronous compatibility."""
    payload = tool_result_data(content)
    if isinstance(payload, ToolResultPayload) and payload.model_wire is not None:
        return payload.model_wire
    # Live provider/history paths await async_prepare_tool_results first. Keep
    # the existing standalone converter API for callers outside those paths.
    return _prepare_tool_result(content)[2]


def model_tool_result_data(content: Any) -> Any:
    """Read bounded presentation data for history maintenance, never raw data."""
    return orjson.loads(serialize_model_tool_result(content))


def compact_tool_json(value: Any) -> str:
    """Serialize model-facing tool data without semantically irrelevant whitespace."""
    # Match Function normalization and provider serialization, including their
    # supported dataclass, datetime and UUID values. Decode before measuring:
    # the model limit is in characters, not UTF-8 bytes.
    return orjson.dumps(value).decode("utf-8")


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


def _compact_json_result_content(
    result: Any, *, integration_owned: bool = False
) -> Any:
    """Compact a ToolResultContent JSON string while preserving parsed semantics."""
    if not integration_owned:
        return result
    tool_result = tool_result_data(result)
    if not isinstance(tool_result, dict):
        return result
    value = tool_result.get("result")
    if not isinstance(value, str):
        return result
    from .runtime_hardening import MAX_MODEL_TOOL_RESULT_CHARACTERS

    # Oversized opaque output will be bounded by the caller. Parsing the entire
    # response here adds event-loop work and allocations that are discarded.
    if len(value) > MAX_MODEL_TOOL_RESULT_CHARACTERS:
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


def bounded_record_page(result: dict[str, Any], records_key: str) -> dict[str, Any]:
    """Fit complete records and their continuation cursor within the wire budget."""
    from .runtime_hardening import MAX_MODEL_TOOL_RESULT_CHARACTERS

    records = result[records_key]
    page = {**result, records_key: []}
    offset = max(0, int(result.get("offset", 0)))
    for record in records:
        page[records_key].append(record)
        returned = len(page[records_key])
        page.update(returned=returned, has_more=True, next_offset=offset + returned)
        # Reserve room for the final boolean/cursor and future wrapper metadata.
        if len(compact_tool_json(page)) > MAX_MODEL_TOOL_RESULT_CHARACTERS - 1024:
            page[records_key].pop()
            break
    returned = len(page[records_key])
    if records and not returned:
        raise ValueError("record exceeds model tool result budget")
    more = bool(result.get("has_more")) or returned < len(records)
    page.update(
        returned=returned,
        has_more=more,
        next_offset=offset + returned if more else None,
    )
    if len(compact_tool_json(page)) > MAX_MODEL_TOOL_RESULT_CHARACTERS:
        raise ValueError("record metadata exceeds model tool result budget")
    return page
