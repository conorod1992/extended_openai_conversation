"""Opt-in metadata diagnostics layered onto the existing request debugger."""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
import time
from typing import Any

from .function_groups import get_function_group_runtime
from .ha_tool_result_compat import tool_result_data
from .payload_diagnostics import (
    approximate_tokens,
    cache_usage_metrics,
    largest_contributors,
    prompt_metrics,
    provider_payload_metrics,
)

_INTERNAL_PROMPT_METRICS = "_payload_prompt_metrics"
_INTERNAL_PREPARATION = "_payload_preparation"
_INTERNAL_TOOL_CALLS = "_payload_tool_calls"
_MODEL_API_SURFACES = {"responses", "chat.completions"}


def _debug_trace() -> Any | None:
    from .debug import current_debug_trace

    return current_debug_trace()


def _record_preparation(
    trace: Any, name: str, duration_ms: int, *, count: int | None = None
) -> None:
    preparation = trace.memory.setdefault(_INTERNAL_PREPARATION, {})
    bucket = preparation.setdefault(
        name, {"calls": 0, "total_ms": 0, "max_ms": 0, "last_count": None}
    )
    bucket["calls"] += 1
    bucket["total_ms"] += duration_ms
    bucket["max_ms"] = max(bucket["max_ms"], duration_ms)
    if count is not None:
        bucket["last_count"] = count


def _result_characters(result: Any) -> int:
    tool_result = tool_result_data(result)
    if not isinstance(tool_result, dict):
        return 0
    value = tool_result.get("result")
    return len(value) if isinstance(value, str) else 0


def _slowest_phases(phases: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {"name": name, "duration_ms": int(duration)}
        for name, duration in phases.items()
        if isinstance(duration, (int, float))
    ]
    return sorted(items, key=lambda item: item["duration_ms"], reverse=True)[:limit]


def _model_requests(trace: Any) -> list[Any]:
    return [
        request
        for request in trace.provider_requests
        if request.api_surface in _MODEL_API_SURFACES
    ]


def record_tool_execution(
    trace: Any,
    function_tool: Any,
    tool_input: Any,
    started: float,
    successful: bool,
    result: Any,
) -> None:
    """Record a completed or interrupted tool attempt without affecting execution."""
    if trace is None:
        return
    try:
        spec = function_tool.get("spec", {}) if isinstance(function_tool, dict) else {}
        implementation = (
            function_tool.get("function", {}) if isinstance(function_tool, dict) else {}
        )
        trace.memory.setdefault(_INTERNAL_TOOL_CALLS, []).append(
            {
                "name": str(
                    spec.get("name") or getattr(tool_input, "tool_name", "unknown")
                ),
                "implementation_type": str(implementation.get("type") or "unknown"),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "successful": successful,
                "result_characters": (_result_characters(result) if successful else 0),
            }
        )
    except Exception:
        pass


def record_provider_metrics(trace: Any, request: Any, api_surface: str) -> Any:
    try:
        safe_kwargs = request.request.get("kwargs", {})
        input_value = safe_kwargs.get("input", safe_kwargs.get("messages"))
        tools = safe_kwargs.get("tools")
        request.metrics.update(provider_payload_metrics(input_value, tools))
        request.metrics["request_index"] = len(trace.provider_requests)
        request.metrics["approx_request_tokens"] = approximate_tokens(
            int(request.metrics.get("request_characters", 0))
        )
        is_model = api_surface in _MODEL_API_SURFACES
        request.metrics["model_request"] = is_model
        request.metrics["explicit_prompt_cache"]["request_cache_key_present"] = bool(
            safe_kwargs.get("prompt_cache_key")
        )
        previous = next(
            (
                candidate
                for candidate in reversed(trace.provider_requests[:-1])
                if candidate.api_surface == api_surface
            ),
            None,
        )
        request.metrics["tools_same_as_previous_request"] = (
            request.metrics.get("tools_sha256") == previous.metrics.get("tools_sha256")
            if previous is not None and is_model
            else None
        )
        request.metrics["model_request_index"] = (
            len(_model_requests(trace)) if is_model else None
        )
    except Exception:
        pass
    return request


def provider_diagnostics(request: Any, data: dict[str, Any]) -> dict[str, Any]:
    try:
        metrics = dict(data.get("metrics", {}))
        metrics["cache_usage"] = cache_usage_metrics(request.usage)
        data["metrics"] = metrics
    except Exception:
        pass
    return data


def trace_diagnostics(trace: Any, data: dict[str, Any]) -> dict[str, Any]:
    try:
        memory = dict(data.get("memory", {}))
        rich_prompt = memory.pop(_INTERNAL_PROMPT_METRICS, None)
        preparation = memory.pop(_INTERNAL_PREPARATION, {})
        tool_calls = memory.pop(_INTERNAL_TOOL_CALLS, [])
        data["memory"] = memory
        if isinstance(rich_prompt, dict):
            data["prompt_metrics"] = {
                **data.get("prompt_metrics", {}),
                **rich_prompt,
            }

        model_requests = [
            item
            for item in data.get("provider_requests", [])
            if isinstance(item, dict) and item.get("api_surface") in _MODEL_API_SURFACES
        ]
        first_model = model_requests[0] if model_requests else {}
        first_metrics = first_model.get("metrics", {})
        input_breakdown = first_metrics.get("input_breakdown", {})
        input_kinds = (
            input_breakdown.get("by_kind", {})
            if isinstance(input_breakdown, dict)
            else {}
        )
        non_prompt_input_kinds = {
            key: value
            for key, value in input_kinds.items()
            if key not in {"system", "developer"}
        }
        data["payload_latency_diagnostics"] = {
            "approximation_notice": (
                "Approximate token counts use characters / 4 and are not provider "
                "billing tokens. Cache ratios use provider-reported token counts only."
            ),
            "model_request_count": len(model_requests),
            "embedding_request_count": sum(
                1
                for item in data.get("provider_requests", [])
                if isinstance(item, dict) and item.get("api_surface") == "embeddings"
            ),
            "preparation": deepcopy(preparation),
            "function_tool_calls": deepcopy(tool_calls),
            "slowest_phases": _slowest_phases(data.get("phases_ms", {})),
            "largest_first_model_request_contributors": largest_contributors(
                prompt_sections=(data.get("prompt_metrics", {}).get("sections") or []),
                tools=(first_metrics.get("tool_breakdown") or []),
                input_kinds=non_prompt_input_kinds,
            ),
        }
    except Exception:
        data["payload_latency_diagnostics"] = {
            "diagnostics_error": "payload_diagnostics_failed"
        }
    return data


def summary_diagnostics(trace: Any, data: dict[str, Any]) -> dict[str, Any]:
    try:
        model_requests = _model_requests(trace)
        first_model = model_requests[0] if model_requests else None
        data["model_request_count"] = len(model_requests)
        data["first_model_request_characters"] = (
            first_model.metrics.get("request_characters")
            if first_model is not None
            else None
        )
        data["first_model_request_approx_input_tokens"] = (
            first_model.metrics.get("approx_input_tokens")
            if first_model is not None
            else None
        )
        model_input_tokens = sum(
            int(request.usage.get("input_tokens", 0)) for request in model_requests
        )
        model_cached_tokens = sum(
            int(request.usage.get("cached_input_tokens", 0))
            for request in model_requests
        )
        data["provider_reported_model_cache_ratio"] = (
            round(model_cached_tokens / model_input_tokens, 4)
            if model_input_tokens
            else None
        )
        slowest = _slowest_phases(trace.phases_ms, limit=1)
        data["slowest_phase"] = slowest[0] if slowest else None
    except Exception:
        pass
    return data


def record_prompt_render(effective: Any, started: float) -> None:
    trace = _debug_trace()
    if trace is not None:
        with suppress(Exception):
            trace.memory[_INTERNAL_PROMPT_METRICS] = prompt_metrics(effective)
            _record_preparation(
                trace, "prompt_render_core", int((time.monotonic() - started) * 1000)
            )


def record_exposed_entities(result: Any, started: float) -> None:
    trace = _debug_trace()
    if trace is not None:
        with suppress(Exception):
            _record_preparation(
                trace,
                "exposed_entity_context",
                int((time.monotonic() - started) * 1000),
                count=len(result) if isinstance(result, list) else None,
            )


def record_tool_assembly(agent: Any, result: Any, started: float) -> None:
    trace = _debug_trace()
    if trace is not None:
        with suppress(Exception):
            _record_preparation(
                trace,
                "function_tool_assembly",
                int((time.monotonic() - started) * 1000),
                count=len(result) if isinstance(result, list) else None,
            )
            runtime = get_function_group_runtime(
                agent.hass, agent.entry.entry_id, agent.subentry.subentry_id
            )
            if runtime is not None:
                trace.memory[_INTERNAL_PREPARATION]["function_groups"] = runtime.stats()
