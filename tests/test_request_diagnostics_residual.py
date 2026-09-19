"""Residual safety coverage for request diagnostics instrumentation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
    debug,
    request_diagnostics,
)


def _trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-residual",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-13T22:00:00+00:00",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
    )


def test_debug_trace_helper_tracks_current_context() -> None:
    assert request_diagnostics._debug_trace() is None
    trace = _trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        assert request_diagnostics._debug_trace() is trace
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

    trace = _trace()
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
    trace = _trace()
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

    result = _trace().summary()

    assert result == base_summary
