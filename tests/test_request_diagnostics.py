"""Tests for request payload/latency diagnostics safety contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from enum import Enum
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
    debug,
    request_diagnostics,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)


class _Marker(Enum):
    VALUE = "value"


def _trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-1",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-11T12:00:00+00:00",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
    )


def test_trace_as_dict_with_diagnostics_empty_trace() -> None:
    trace = _trace()

    result = trace.as_dict()

    assert result["debug_id"] == "debug-1"
    assert result["payload_latency_diagnostics"]["model_request_count"] == 0
    assert result["payload_latency_diagnostics"]["embedding_request_count"] == 0
    assert result["payload_latency_diagnostics"]["preparation"] == {}
    assert result["payload_latency_diagnostics"]["function_tool_calls"] == []
    assert (
        result["payload_latency_diagnostics"][
            "largest_first_model_request_contributors"
        ]
        == []
    )


def test_trace_as_dict_with_diagnostics_populated_payloads() -> None:
    trace = _trace()
    trace.phases_ms = {"provider": 25, "prompt": 5}
    trace.memory = {
        "visible": {"count": 1},
        request_diagnostics._INTERNAL_PREPARATION: {
            "prompt_render_core": {
                "calls": 1,
                "total_ms": 4,
                "max_ms": 4,
                "last_count": None,
            }
        },
        request_diagnostics._INTERNAL_TOOL_CALLS: [
            {
                "name": "demo",
                "implementation_type": "native",
                "duration_ms": 3,
                "successful": True,
                "result_characters": 2,
            }
        ],
    }
    original_memory = deepcopy(trace.memory)

    request = trace.start_provider_request(
        "responses",
        (),
        {
            "input": [{"role": "user", "content": "hello world"}],
            "tools": [
                {
                    "type": "function",
                    "name": "demo",
                    "parameters": {"type": "object"},
                }
            ],
        },
    )

    result = trace.as_dict()

    assert trace.memory == original_memory
    assert result["memory"] == {"visible": {"count": 1}}
    assert request.metrics["approx_input_tokens"] > 0
    assert request.metrics["tool_breakdown"][0]["name"] == "demo"
    diagnostics = result["payload_latency_diagnostics"]
    assert diagnostics["model_request_count"] == 1
    assert (
        diagnostics["preparation"]
        == original_memory[request_diagnostics._INTERNAL_PREPARATION]
    )
    assert (
        diagnostics["function_tool_calls"]
        == original_memory[request_diagnostics._INTERNAL_TOOL_CALLS]
    )
    assert diagnostics["slowest_phases"][0] == {
        "name": "provider",
        "duration_ms": 25,
    }


def test_trace_as_dict_falls_back_when_trace_serialization_raises(monkeypatch) -> None:
    def broken_as_dict(_value, **_kwargs) -> dict[str, object]:
        raise RuntimeError("serializer failed with SECRET_RAW_PAYLOAD")

    monkeypatch.setattr(debug, "_jsonable", broken_as_dict)

    result = _trace().as_dict()

    assert result == {
        "debug_id": "debug-1",
        "diagnostics_error": "trace_serialization_failed",
    }
    assert "SECRET_RAW_PAYLOAD" not in json.dumps(result)


def test_trace_as_dict_diagnostics_failure_uses_safe_serialized_base(
    monkeypatch,
) -> None:
    trace = _trace()
    trace.memory = {"api_key": "CANARY_SECRET", "ordinary": "safe"}

    def broken_largest_contributors(**_kwargs):
        raise RuntimeError("diagnostics failed")

    monkeypatch.setattr(
        request_diagnostics, "largest_contributors", broken_largest_contributors
    )

    result = trace.as_dict()
    serialized = json.dumps(result, sort_keys=True)

    assert result["payload_latency_diagnostics"] == {
        "diagnostics_error": "payload_diagnostics_failed"
    }
    assert result["memory"]["ordinary"] == "safe"
    assert "CANARY_SECRET" not in serialized


def test_trace_as_dict_never_leaks_nested_secrets() -> None:
    trace = _trace()
    secrets = {
        "authorization": "CANARY_AUTHORIZATION",
        "api_key": "CANARY_API_KEY",
        "x-api-key": "CANARY_NESTED_KEY",
        "proxy_authorization": "CANARY_PROXY_AUTH",
    }

    trace.start_provider_request(
        "responses",
        (),
        {
            "input": [
                {
                    "role": "user",
                    "content": "hello",
                    "metadata": {
                        "credentials": {
                            "api_key": secrets["api_key"],
                            "nested": [
                                {"proxy_authorization": secrets["proxy_authorization"]}
                            ],
                        }
                    },
                }
            ],
            "headers": {"Authorization": secrets["authorization"]},
            "tools": [
                {
                    "type": "function",
                    "name": "demo",
                    "metadata": {"x-api-key": secrets["x-api-key"]},
                }
            ],
        },
    )

    serialized = json.dumps(trace.as_dict(), sort_keys=True)

    for secret in secrets.values():
        assert secret not in serialized


def test_trace_as_dict_handles_non_json_native_values() -> None:
    trace = _trace()
    value = datetime(2026, 9, 11, 12, 30, tzinfo=UTC)
    trace.memory = {"when": value, "marker": _Marker.VALUE}

    result = trace.as_dict()

    assert result["memory"]["when"] == value.isoformat()
    assert isinstance(result["memory"]["marker"], dict)
    json.dumps(result)


def test_request_diagnostics_metric_failure_does_not_break_recording(
    monkeypatch,
) -> None:
    trace = _trace()

    def broken_metrics(_input, _tools):
        raise RuntimeError("metric helper failed")

    monkeypatch.setattr(request_diagnostics, "provider_payload_metrics", broken_metrics)

    request = trace.start_provider_request(
        "responses", (), {"input": [{"role": "user", "content": "hello"}]}
    )

    assert trace.provider_requests == [request]
    assert request.api_surface == "responses"
    assert request.metrics["request_characters"] > 0
    assert "approx_input_tokens" not in request.metrics


def _coverage_trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-coverage",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-13T10:00:00+00:00",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
    )


def test_helper_aggregation_filters_and_orders_values() -> None:
    trace = _coverage_trace()
    request_diagnostics._record_preparation(trace, "prompt", 4)
    request_diagnostics._record_preparation(trace, "prompt", 9, count=3)

    assert trace.memory[request_diagnostics._INTERNAL_PREPARATION]["prompt"] == {
        "calls": 2,
        "total_ms": 13,
        "max_ms": 9,
        "last_count": 3,
    }
    assert (
        request_diagnostics._result_characters(
            SimpleNamespace(tool_result={"result": "hello"})
        )
        == 5
    )
    assert (
        request_diagnostics._result_characters(
            SimpleNamespace(tool_result={"result": 123})
        )
        == 0
    )
    assert request_diagnostics._result_characters(SimpleNamespace()) == 0
    assert request_diagnostics._slowest_phases(
        {"slow": 20.9, "fast": 2, "ignored": "not-a-number"}, limit=1
    ) == [{"name": "slow", "duration_ms": 20}]


@pytest.mark.asyncio
async def test_tool_execution_records_success_and_failure_without_changing_semantics(
    monkeypatch,
) -> None:
    trace = _coverage_trace()
    agent_type = conversation_module.ExtendedOpenAIAgentEntity

    async def original_execute(
        _agent, _function_tool, tool_input, _llm_context, _exposed_entities
    ):
        if getattr(tool_input, "fail", False):
            raise RuntimeError("tool failed")
        return SimpleNamespace(tool_result={"result": "done"})

    monkeypatch.setattr(request_diagnostics, "_debug_trace", lambda: trace)
    monkeypatch.setattr(conversation_module, "current_debug_trace", lambda: trace)
    monkeypatch.setattr(agent_type, "_async_dispatch_function_tool", original_execute)

    agent = object.__new__(agent_type)
    tool = {"spec": {"name": "demo"}, "function": {"type": "native"}}
    result = await agent_type._execute_function_tool(
        agent,
        tool,
        SimpleNamespace(tool_name="fallback", fail=False),
        None,
        None,
    )
    assert tool_result_data(result)["result"] == "done"

    with pytest.raises(RuntimeError, match="tool failed"):
        await agent_type._execute_function_tool(
            agent,
            tool,
            SimpleNamespace(tool_name="fallback", fail=True),
            None,
            None,
        )

    calls = trace.memory[request_diagnostics._INTERNAL_TOOL_CALLS]
    assert calls[0]["name"] == "demo"
    assert calls[0]["implementation_type"] == "native"
    assert calls[0]["successful"] is True
    assert calls[0]["result_characters"] == 4
    assert calls[1]["successful"] is False
    assert calls[1]["result_characters"] == 0


def test_provider_request_metrics_compare_rounds_and_serialize_cache_usage() -> None:
    trace = _coverage_trace()
    tools = [{"type": "function", "name": "demo", "parameters": {"type": "object"}}]

    first = trace.start_provider_request(
        "responses",
        (),
        {
            "input": [{"role": "user", "content": "first"}],
            "tools": tools,
            "prompt_cache_key": "cache-key",
        },
    )
    second = trace.start_provider_request(
        "responses",
        (),
        {"input": [{"role": "user", "content": "second"}], "tools": tools},
    )
    embedding = trace.start_provider_request("embeddings", (), {"input": "vector me"})

    assert first.metrics["request_index"] == 1
    assert first.metrics["model_request"] is True
    assert first.metrics["explicit_prompt_cache"]["request_cache_key_present"] is True
    assert first.metrics["tools_same_as_previous_request"] is None
    assert first.metrics["model_request_index"] == 1
    assert second.metrics["request_index"] == 2
    assert second.metrics["tools_same_as_previous_request"] is True
    assert second.metrics["model_request_index"] == 2
    assert embedding.metrics["model_request"] is False
    assert embedding.metrics["model_request_index"] is None

    first.usage.update({"input_tokens": 100, "cached_input_tokens": 25})
    serialized = first.as_dict()
    cache = serialized["metrics"]["cache_usage"]
    assert cache["provider_reported_cached_input_tokens"] == 25
    assert cache["provider_reported_cache_ratio"] == 0.25


def test_trace_and_summary_expose_aggregated_model_diagnostics() -> None:
    trace = _coverage_trace()
    trace.phases_ms = {"prepare": 7, "provider": 30}
    trace.memory[request_diagnostics._INTERNAL_PROMPT_METRICS] = {
        "characters": 120,
        "approx_tokens": 30,
        "sections": [{"name": "system", "characters": 120, "approx_tokens": 30}],
    }

    first = trace.start_provider_request(
        "responses",
        (),
        {"input": [{"role": "user", "content": "hello"}], "tools": []},
    )
    second = trace.start_provider_request(
        "chat.completions",
        (),
        {"messages": [{"role": "user", "content": "again"}], "tools": []},
    )
    trace.start_provider_request("embeddings", (), {"input": "embed"})
    first.usage.update({"input_tokens": 100, "cached_input_tokens": 40})
    second.usage.update({"input_tokens": 50, "cached_input_tokens": 10})

    detailed = trace.as_dict()
    assert detailed["prompt_metrics"]["characters"] == 120
    diagnostics = detailed["payload_latency_diagnostics"]
    assert diagnostics["model_request_count"] == 2
    assert diagnostics["embedding_request_count"] == 1
    assert diagnostics["slowest_phases"][0] == {
        "name": "provider",
        "duration_ms": 30,
    }

    summary = trace.summary()
    assert summary["model_request_count"] == 2
    assert (
        summary["first_model_request_characters"] == first.metrics["request_characters"]
    )
    assert (
        summary["first_model_request_approx_input_tokens"]
        == first.metrics["approx_input_tokens"]
    )
    assert summary["provider_reported_model_cache_ratio"] == pytest.approx(0.3333)
    assert summary["slowest_phase"] == {"name": "provider", "duration_ms": 30}



def _residual_trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-residual",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-13T22:00:00+00:00",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
    )


def test_debug_trace_helper_tracks_current_context() -> None:
    assert request_diagnostics._debug_residual_trace() is None
    trace = _residual_trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        assert request_diagnostics._debug_residual_trace() is trace
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)


@pytest.mark.asyncio
async def test_execute_wrapper_passthrough_without_trace(monkeypatch) -> None:
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    expected = SimpleNamespace(tool_result={"result": "ok"})
    original_execute = AsyncMock(return_value=expected)
    monkeypatch.setattr(agent_type, "_async_dispatch_function_tool", original_execute)

    result = await agent_type._execute_function_tool(
        object.__new__(agent_type), {}, SimpleNamespace(tool_name="demo"), object(), {}
    )

    assert result is expected
    original_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_diagnostic_failure_never_masks_tool_result(monkeypatch) -> None:
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    expected = SimpleNamespace(tool_result={"result": "ok"})
    monkeypatch.setattr(
        agent_type, "_async_dispatch_function_tool", AsyncMock(return_value=expected)
    )
    monkeypatch.setattr(
        request_diagnostics,
        "_result_characters",
        Mock(side_effect=RuntimeError("measurement failed")),
    )

    trace = _residual_trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        result = await agent_type._execute_function_tool(
            object.__new__(agent_type),
            {"spec": {"name": "demo"}, "function": {"type": "native"}},
            SimpleNamespace(tool_name="demo"),
            object(),
            {},
        )
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)

    assert result is expected
    assert trace.memory[request_diagnostics._INTERNAL_TOOL_CALLS] == []


def test_provider_serialization_cache_metric_failure_is_transparent(
    monkeypatch,
) -> None:
    trace = _residual_trace()
    request = trace.start_provider_request(
        "responses", (), {"input": [{"role": "user", "content": "hello"}]}
    )
    monkeypatch.setattr(
        request_diagnostics,
        "cache_usage_metrics",
        Mock(side_effect=RuntimeError("cache metrics failed")),
    )

    result = request.as_dict()

    assert result["api_surface"] == "responses"
    assert "cache_usage" not in result.get("metrics", {})


def test_summary_metric_failure_keeps_original_summary(monkeypatch) -> None:
    base_summary = {"debug_id": "debug-residual", "status": "complete"}
    monkeypatch.setattr(
        debug.DebugTrace, "summary", Mock(return_value=base_summary.copy())
    )
    monkeypatch.setattr(
        request_diagnostics,
        "_model_requests",
        Mock(side_effect=RuntimeError("summary metrics failed")),
    )

    result = _residual_trace().summary()

    assert result == base_summary
