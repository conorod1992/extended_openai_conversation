"""Bounded management projection for volatile request-debug captures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .management_result_limits import (
    MANAGEMENT_DEBUG_PAGE_CHARACTERS,
    MANAGEMENT_DEBUG_PROVIDER_PAGE_DEFAULT,
    MANAGEMENT_DEBUG_PROVIDER_PAGE_MAX,
    MANAGEMENT_DEBUG_SUMMARY_VALUE_CHARACTERS,
    MANAGEMENT_DEBUG_TEXT_CHARACTERS,
    MANAGEMENT_DEBUG_VALUE_CHARACTERS,
    page_metadata,
)

_TRUNCATED_TEXT = "<management debug value truncated>"
_MAX_CONTAINER_ITEMS = 256
_MAX_DEPTH = 16


@dataclass(slots=True)
class _ProjectionBudget:
    remaining: int
    truncated: bool = False

    def consume(self, amount: int) -> bool:
        amount = max(0, int(amount))
        if amount <= self.remaining:
            self.remaining -= amount
            return True
        self.remaining = 0
        self.truncated = True
        return False


def _bounded_text(
    value: Any,
    limit: int,
    page_budget: _ProjectionBudget | None = None,
) -> tuple[str | None, dict[str, Any]]:
    if value is None:
        return None, {"truncated": False, "limit_characters": limit}
    text = str(value)
    allowance = min(limit, page_budget.remaining if page_budget is not None else limit)
    kept = min(len(text), max(0, allowance))
    if page_budget is not None:
        page_budget.remaining = max(0, page_budget.remaining - kept)
    truncated = kept < len(text)
    if truncated and page_budget is not None and allowance < limit:
        page_budget.truncated = True
    if not truncated:
        return text, {"truncated": False, "limit_characters": limit}
    return (
        text[:kept] + "\n<management debug text truncated>",
        {
            "truncated": True,
            "limit_characters": limit,
            "original_characters": len(text),
        },
    )


def _project_value(value: Any, budget: _ProjectionBudget, depth: int = 0) -> Any:
    """Preserve JSON-like structure while respecting one character budget."""
    if depth > _MAX_DEPTH:
        budget.truncated = True
        return "<management debug depth limit reached>"
    if value is None or isinstance(value, bool | int | float):
        budget.consume(len(str(value)))
        return value
    if isinstance(value, str):
        allowance = min(len(value), budget.remaining)
        if allowance < len(value):
            budget.truncated = True
            text = value[:allowance]
            budget.remaining = 0
            return text + _TRUNCATED_TEXT
        budget.remaining -= allowance
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= _MAX_CONTAINER_ITEMS or budget.remaining <= 0:
                budget.truncated = True
                break
            key = str(raw_key)
            if not budget.consume(len(key)):
                break
            result[key] = _project_value(item, budget, depth + 1)
        if len(result) < len(value):
            result["__management_truncated__"] = True
        return result
    if isinstance(value, (list, tuple)):
        result: list[Any] = []
        for index, item in enumerate(value):
            if index >= _MAX_CONTAINER_ITEMS or budget.remaining <= 0:
                budget.truncated = True
                break
            result.append(_project_value(item, budget, depth + 1))
        if len(result) < len(value):
            result.append(_TRUNCATED_TEXT)
        return result
    return _project_value(str(value), budget, depth + 1)


def _bounded_value(
    value: Any,
    limit: int,
    page_budget: _ProjectionBudget | None = None,
) -> tuple[Any, dict[str, Any]]:
    allowance = min(limit, page_budget.remaining if page_budget is not None else limit)
    budget = _ProjectionBudget(max(0, allowance))
    projected = _project_value(value, budget)
    consumed = max(0, allowance - budget.remaining)
    if page_budget is not None:
        page_budget.remaining = max(0, page_budget.remaining - consumed)
        if budget.truncated and allowance < limit:
            page_budget.truncated = True
    return projected, {
        "truncated": budget.truncated,
        "limit_characters": limit,
    }


def _summary(trace: Any) -> dict[str, Any]:
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    for request in trace.provider_requests:
        for key in usage:
            usage[key] += int(request.usage.get(key, 0))
    error, error_meta = _bounded_value(
        trace.error, MANAGEMENT_DEBUG_SUMMARY_VALUE_CHARACTERS
    )
    first_request = trace.provider_requests[0] if trace.provider_requests else None
    return {
        "debug_id": trace.debug_id,
        "started_at": trace.started_at,
        "completed_at": trace.completed_at,
        "duration_ms": trace.duration_ms,
        "successful": trace.successful,
        "error_type": trace.error_type,
        "error": error,
        "error_meta": error_meta,
        "usage_run_id": trace.usage_run_id,
        "incoming_conversation_id": trace.incoming_conversation_id,
        "resolved_conversation_id": trace.continuity.get("resolved_conversation_id"),
        "continuity_resumed": trace.continuity.get("resumed"),
        "continuity_mode": trace.continuity.get("mode"),
        "provider_request_count": len(trace.provider_requests),
        "first_text_ms": (
            first_request.first_text_ms if first_request is not None else None
        ),
        **usage,
    }


def debug_run_summaries(manager: Any) -> list[dict[str, Any]]:
    """Return bounded summary rows without serializing complete run errors."""
    return [_summary(trace) for trace in reversed(manager._runs)]


def _provider_request(
    request: Any, page_budget: _ProjectionBudget
) -> dict[str, Any]:
    request_value, request_meta = _bounded_value(
        request.request, MANAGEMENT_DEBUG_VALUE_CHARACTERS, page_budget
    )
    response_value, response_meta = _bounded_value(
        request.response_events, MANAGEMENT_DEBUG_VALUE_CHARACTERS, page_budget
    )
    error_value, error_meta = _bounded_value(
        request.error, MANAGEMENT_DEBUG_VALUE_CHARACTERS, page_budget
    )
    return {
        "request_id": request.request_id,
        "api_surface": request.api_surface,
        "started_at": request.started_at,
        "started_offset_ms": request.started_offset_ms,
        "request": request_value,
        "request_meta": {
            **request_meta,
            "captured_characters": request.metrics.get("request_characters"),
        },
        "metrics": dict(request.metrics),
        "stream_open_ms": request.stream_open_ms,
        "first_event_ms": request.first_event_ms,
        "first_text_ms": request.first_text_ms,
        "first_action_ms": request.first_action_ms,
        "duration_ms": request.duration_ms,
        "successful": request.successful,
        "error_type": request.error_type,
        "error": error_value,
        "error_meta": error_meta,
        "usage": dict(request.usage),
        "response_events": response_value,
        "response_events_meta": {
            **response_meta,
            "capture_truncated": bool(request.response_events_truncated),
            "captured_characters": max(0, int(request._event_bytes)),
        },
    }


def debug_trace_page(
    manager: Any,
    debug_id: str,
    *,
    provider_offset: int = 0,
    provider_limit: int = MANAGEMENT_DEBUG_PROVIDER_PAGE_DEFAULT,
) -> dict[str, Any] | None:
    """Project one debug run with a hard aggregate budget and paged provider traces."""
    trace = next((item for item in manager._runs if item.debug_id == debug_id), None)
    if trace is None:
        return None
    safe_offset = max(0, int(provider_offset))
    safe_limit = max(1, min(int(provider_limit), MANAGEMENT_DEBUG_PROVIDER_PAGE_MAX))
    provider_page = trace.provider_requests[safe_offset : safe_offset + safe_limit]
    page_budget = _ProjectionBudget(MANAGEMENT_DEBUG_PAGE_CHARACTERS)

    system_prompt, system_prompt_meta = _bounded_text(
        trace.system_prompt, MANAGEMENT_DEBUG_TEXT_CHARACTERS, page_budget
    )
    field_values: dict[str, Any] = {}
    field_meta: dict[str, Any] = {}

    # Project the small request-context fields before provider data so a large
    # memory/result object cannot consume the page before the provider trace.
    for name, value in (
        ("error", trace.error),
        ("user_input", trace.user_input),
        ("continuity", trace.continuity),
        ("phases_ms", trace.phases_ms),
        ("prompt_metrics", trace.prompt_metrics),
    ):
        field_values[name], field_meta[name] = _bounded_value(
            value, MANAGEMENT_DEBUG_VALUE_CHARACTERS, page_budget
        )

    providers = [_provider_request(request, page_budget) for request in provider_page]

    for name, value in (
        ("memory", trace.memory),
        ("result", trace.result),
        ("notes", trace.notes),
    ):
        field_values[name], field_meta[name] = _bounded_value(
            value, MANAGEMENT_DEBUG_VALUE_CHARACTERS, page_budget
        )

    provider_meta = page_metadata(
        offset=safe_offset,
        limit=safe_limit,
        returned=len(providers),
        has_more=len(trace.provider_requests) > safe_offset + len(providers),
        total=len(trace.provider_requests),
    )
    field_truncated = any(meta.get("truncated") for meta in field_meta.values())
    provider_truncated = any(
        request["request_meta"].get("truncated")
        or request["response_events_meta"].get("truncated")
        or request["response_events_meta"].get("capture_truncated")
        or request["error_meta"].get("truncated")
        for request in providers
    )
    trace_result = {
        "debug_id": trace.debug_id,
        "entry_id": trace.entry_id,
        "subentry_id": trace.subentry_id,
        "started_at": trace.started_at,
        "completed_at": trace.completed_at,
        "duration_ms": trace.duration_ms,
        "successful": trace.successful,
        "error_type": trace.error_type,
        "usage_run_id": trace.usage_run_id,
        "incoming_conversation_id": trace.incoming_conversation_id,
        "system_prompt": system_prompt,
        "provider_requests": providers,
        **field_values,
        "management_projection": {
            "page": {
                "limit_characters": MANAGEMENT_DEBUG_PAGE_CHARACTERS,
                "remaining_characters": page_budget.remaining,
                "truncated": page_budget.truncated,
            },
            "system_prompt": system_prompt_meta,
            "fields": field_meta,
            "provider_requests": provider_meta,
            "truncated": bool(
                page_budget.truncated
                or system_prompt_meta.get("truncated")
                or field_truncated
                or provider_truncated
                or provider_meta["has_more"]
            ),
        },
    }
    return trace_result
