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


@pytest.fixture(autouse=True)
def restore_wrappers():
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    originals = {
        "render": conversation_module.render_effective_prompt,
        "exposed": agent_type._get_exposed_entities,
        "tools": agent_type._get_function_tools,
        "execute": agent_type._execute_function_tool,
        "start_provider": debug.DebugTrace.start_provider_request,
        "provider_as_dict": debug.DebugProviderRequest.as_dict,
        "trace_as_dict": debug.DebugTrace.as_dict,
        "summary": debug.DebugTrace.summary,
        "installed": request_diagnostics._INSTALLED,
    }
    request_diagnostics._INSTALLED = False
    try:
        yield
    finally:
        conversation_module.render_effective_prompt = originals["render"]
        agent_type._get_exposed_entities = originals["exposed"]
        agent_type._get_function_tools = originals["tools"]
        agent_type._execute_function_tool = originals["execute"]
        debug.DebugTrace.start_provider_request = originals["start_provider"]
        debug.DebugProviderRequest.as_dict = originals["provider_as_dict"]
        debug.DebugTrace.as_dict = originals["trace_as_dict"]
        debug.DebugTrace.summary = originals["summary"]
        request_diagnostics._INSTALLED = originals["installed"]


def test_debug_trace_helper_tracks_current_context() -> None:
    assert request_diagnostics._debug_trace() is None
    trace = _trace()
    token = debug._CURRENT_DEBUG_TRACE.set(trace)
    try:
        assert request_diagnostics._debug_trace() is trace
    finally:
        debug._CURRENT_DEBUG_TRACE.reset(token)


def test_render_metric_failure_is_transparent(monkeypatch) -> None:
    rendered = {"prompt": "effective"}
    monkeypatch.setattr(
        conversation_module, "render_effective_prompt", Mock(return_value=rendered)
    )
    request_diagnostics.install_payload_latency_diagnostics()
    monkeypatch.setattr(
        request_diagnostics,
        "prompt_metrics",
        Mock(side_effect=RuntimeError("diagnostics failed")),
    )
    trace = _trace()
    token = debug._CURRENT_DEBUG_TRACE.set(trace)
    try:
        result = conversation_module.render_effective_prompt("input")
    finally:
        debug._CURRENT_DEBUG_TRACE.reset(token)

    assert result is rendered
    assert request_diagnostics._INTERNAL_PROMPT_METRICS not in trace.memory


def test_tool_assembly_records_function_group_stats(monkeypatch) -> None:
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    original_tools = Mock(return_value=[{"spec": {"name": "demo"}}])
    monkeypatch.setattr(agent_type, "_get_function_tools", original_tools)
    runtime = SimpleNamespace(stats=Mock(return_value={"groups": 2}))
    monkeypatch.setattr(
        request_diagnostics, "get_function_group_runtime", Mock(return_value=runtime)
    )
    request_diagnostics.install_payload_latency_diagnostics()

    agent = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-1"),
    )
    trace = _trace()
    token = debug._CURRENT_DEBUG_TRACE.set(trace)
    try:
        result = agent_type._get_function_tools(agent)
    finally:
        debug._CURRENT_DEBUG_TRACE.reset(token)

    assert result == [{"spec": {"name": "demo"}}]
    preparation = trace.memory[request_diagnostics._INTERNAL_PREPARATION]
    assert preparation["function_tool_assembly"]["last_count"] == 1
    assert preparation["function_groups"] == {"groups": 2}
    runtime.stats.assert_called_once_with()


@pytest.mark.asyncio
async def test_execute_wrapper_passthrough_without_trace(monkeypatch) -> None:
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    expected = SimpleNamespace(tool_result={"result": "ok"})
    original_execute = AsyncMock(return_value=expected)
    monkeypatch.setattr(agent_type, "_execute_function_tool", original_execute)
    request_diagnostics.install_payload_latency_diagnostics()

    result = await agent_type._execute_function_tool(
        SimpleNamespace(), {}, SimpleNamespace(tool_name="demo"), object(), {}
    )

    assert result is expected
    original_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_diagnostic_failure_never_masks_tool_result(monkeypatch) -> None:
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    expected = SimpleNamespace(tool_result={"result": "ok"})
    monkeypatch.setattr(
        agent_type, "_execute_function_tool", AsyncMock(return_value=expected)
    )
    request_diagnostics.install_payload_latency_diagnostics()
    monkeypatch.setattr(
        request_diagnostics,
        "_result_characters",
        Mock(side_effect=RuntimeError("measurement failed")),
    )

    trace = _trace()
    token = debug._CURRENT_DEBUG_TRACE.set(trace)
    try:
        result = await agent_type._execute_function_tool(
            SimpleNamespace(),
            {"spec": {"name": "demo"}, "function": {"type": "native"}},
            SimpleNamespace(tool_name="demo"),
            object(),
            {},
        )
    finally:
        debug._CURRENT_DEBUG_TRACE.reset(token)

    assert result is expected
    assert request_diagnostics._INTERNAL_TOOL_CALLS not in trace.memory


def test_provider_serialization_cache_metric_failure_is_transparent(monkeypatch) -> None:
    request_diagnostics.install_payload_latency_diagnostics()
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
    monkeypatch.setattr(debug.DebugTrace, "summary", Mock(return_value=base_summary.copy()))
    request_diagnostics.install_payload_latency_diagnostics()
    monkeypatch.setattr(
        request_diagnostics,
        "_model_requests",
        Mock(side_effect=RuntimeError("summary metrics failed")),
    )

    result = _trace().summary()

    assert result == base_summary
