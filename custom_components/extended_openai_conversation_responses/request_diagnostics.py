"""Opt-in metadata diagnostics layered onto the existing request debugger."""

from __future__ import annotations

from copy import deepcopy
import time
from typing import Any

from .function_groups import get_function_group_runtime
from .payload_diagnostics import (
    approximate_tokens,
    cache_usage_metrics,
    largest_contributors,
    prompt_metrics,
    provider_payload_metrics,
)

_INSTALLED = False
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
    tool_result = getattr(result, "tool_result", None)
    if not isinstance(tool_result, dict):
        return 0
    value = tool_result.get("result")
    return len(value) if isinstance(value, str) else 0


def _slowest_phases(phases: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    items = [
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


def install_payload_latency_diagnostics() -> None:
    """Install debug-only measurements without changing model/request semantics."""
    global _INSTALLED
    if _INSTALLED:
        return

    from . import conversation as conversation_module, debug

    agent_type: Any = conversation_module.ExtendedOpenAIAgentEntity
    trace_type: Any = debug.DebugTrace
    provider_request_type: Any = debug.DebugProviderRequest

    original_render = conversation_module.render_effective_prompt
    original_exposed = agent_type._get_exposed_entities
    original_tools = agent_type._get_function_tools
    original_execute = agent_type._execute_function_tool
    original_start_provider = trace_type.start_provider_request
    original_provider_as_dict = provider_request_type.as_dict
    original_trace_as_dict = trace_type.as_dict
    original_summary = trace_type.summary

    def render_with_metrics(*args: Any, **kwargs: Any) -> Any:
        trace = _debug_trace()
        if trace is None:
            return original_render(*args, **kwargs)
        started = time.monotonic()
        effective = original_render(*args, **kwargs)
        render_ms = int((time.monotonic() - started) * 1000)
        trace.memory[_INTERNAL_PROMPT_METRICS] = prompt_metrics(effective)
        _record_preparation(trace, "prompt_render_core", render_ms)
        return effective

    def exposed_with_timing(agent: Any, *args: Any, **kwargs: Any) -> Any:
        trace = _debug_trace()
        if trace is None:
            return original_exposed(agent, *args, **kwargs)
        started = time.monotonic()
        result = original_exposed(agent, *args, **kwargs)
        _record_preparation(
            trace,
            "exposed_entity_context",
            int((time.monotonic() - started) * 1000),
            count=len(result) if isinstance(result, list) else None,
        )
        return result

    def tools_with_timing(agent: Any, *args: Any, **kwargs: Any) -> Any:
        trace = _debug_trace()
        if trace is None:
            return original_tools(agent, *args, **kwargs)
        started = time.monotonic()
        result = original_tools(agent, *args, **kwargs)
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
        return result

    async def execute_with_timing(
        agent: Any,
        function_tool: Any,
        tool_input: Any,
        llm_context: Any,
        exposed_entities: Any,
    ) -> Any:
        trace = _debug_trace()
        if trace is None:
            return await original_execute(
                agent, function_tool, tool_input, llm_context, exposed_entities
            )
        started = time.monotonic()
        successful = False
        result: Any = None
        try:
            result = await original_execute(
                agent, function_tool, tool_input, llm_context, exposed_entities
            )
            successful = True
            return result
        finally:
            spec = (
                function_tool.get("spec", {}) if isinstance(function_tool, dict) else {}
            )
            implementation = (
                function_tool.get("function", {})
                if isinstance(function_tool, dict)
                else {}
            )
            trace.memory.setdefault(_INTERNAL_TOOL_CALLS, []).append(
                {
                    "name": str(
                        spec.get("name") or getattr(tool_input, "tool_name", "unknown")
                    ),
                    "implementation_type": str(implementation.get("type") or "unknown"),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "successful": successful,
                    "result_characters": (
                        _result_characters(result) if successful else 0
                    ),
                }
            )

    def start_provider_with_metrics(
        trace: Any, api_surface: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        request = original_start_provider(trace, api_surface, args, kwargs)
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
        return request

    def provider_as_dict_with_cache(request: Any) -> dict[str, Any]:
        data = original_provider_as_dict(request)
        metrics = dict(data.get("metrics", {}))
        metrics["cache_usage"] = cache_usage_metrics(request.usage)
        data["metrics"] = metrics
        return data

    def trace_as_dict_with_diagnostics(trace: Any) -> dict[str, Any]:
        data = original_trace_as_dict(trace)
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
        # System/developer input duplicates the more actionable prompt-section split.
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
        return data

    def summary_with_diagnostics(trace: Any) -> dict[str, Any]:
        data = original_summary(trace)
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
        return data

    conversation_module.render_effective_prompt = render_with_metrics
    agent_type._get_exposed_entities = exposed_with_timing
    agent_type._get_function_tools = tools_with_timing
    agent_type._execute_function_tool = execute_with_timing
    trace_type.start_provider_request = start_provider_with_metrics
    provider_request_type.as_dict = provider_as_dict_with_cache
    trace_type.as_dict = trace_as_dict_with_diagnostics
    trace_type.summary = summary_with_diagnostics
    _INSTALLED = True
