"""Focused residual coverage for request diagnostics wrappers and summaries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as conversation_module,
    debug,
    request_diagnostics,
)


def _trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-coverage",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-13T10:00:00+00:00",
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


def test_helper_aggregation_filters_and_orders_values() -> None:
    trace = _trace()
    request_diagnostics._record_preparation(trace, "prompt", 4)
    request_diagnostics._record_preparation(trace, "prompt", 9, count=3)

    assert trace.memory[request_diagnostics._INTERNAL_PREPARATION]["prompt"] == {
        "calls": 2,
        "total_ms": 13,
        "max_ms": 9,
        "last_count": 3,
    }
    assert request_diagnostics._result_characters(
        SimpleNamespace(tool_result={"result": "hello"})
    ) == 5
    assert request_diagnostics._result_characters(
        SimpleNamespace(tool_result={"result": 123})
    ) == 0
    assert request_diagnostics._result_characters(SimpleNamespace()) == 0
    assert request_diagnostics._slowest_phases(
        {"slow": 20.9, "fast": 2, "ignored": "not-a-number"}, limit=1
    ) == [{"name": "slow", "duration_ms": 20}]


def test_prompt_entity_and_tool_preparation_wrappers_record_debug_metrics(monkeypatch) -> None:
    trace = _trace()
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    prompt = SimpleNamespace(text="Rendered prompt text", sections=[])

    monkeypatch.setattr(request_diagnostics, "_debug_trace", lambda: trace)
    monkeypatch.setattr(
        conversation_module,
        "render_effective_prompt",
        lambda *_args, **_kwargs: prompt,
    )
    monkeypatch.setattr(
        agent_type,
        "_get_exposed_entities",
        lambda _agent, *_args, **_kwargs: [{"entity_id": "light.kitchen"}],
    )
    monkeypatch.setattr(
        agent_type,
        "_get_function_tools",
        lambda _agent, *_args, **_kwargs: [{"spec": {"name": "demo"}}],
    )
    monkeypatch.setattr(
        request_diagnostics,
        "get_function_group_runtime",
        lambda *_args: SimpleNamespace(stats=lambda: {"loaded_groups": 2}),
    )

    request_diagnostics.install_payload_latency_diagnostics()

    agent = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-1"),
    )
    assert conversation_module.render_effective_prompt() is prompt
    assert agent_type._get_exposed_entities(agent) == [{"entity_id": "light.kitchen"}]
    assert agent_type._get_function_tools(agent) == [{"spec": {"name": "demo"}}]

    preparation = trace.memory[request_diagnostics._INTERNAL_PREPARATION]
    assert preparation["prompt_render_core"]["calls"] == 1
    assert preparation["exposed_entity_context"]["last_count"] == 1
    assert preparation["function_tool_assembly"]["last_count"] == 1
    assert preparation["function_groups"] == {"loaded_groups": 2}
    assert trace.memory[request_diagnostics._INTERNAL_PROMPT_METRICS]["characters"] == len(
        prompt.text
    )


@pytest.mark.asyncio
async def test_tool_execution_records_success_and_failure_without_changing_semantics(
    monkeypatch,
) -> None:
    trace = _trace()
    agent_type = conversation_module.ExtendedOpenAIAgentEntity

    async def original_execute(
        _agent, _function_tool, tool_input, _llm_context, _exposed_entities
    ):
        if getattr(tool_input, "fail", False):
            raise RuntimeError("tool failed")
        return SimpleNamespace(tool_result={"result": "done"})

    monkeypatch.setattr(request_diagnostics, "_debug_trace", lambda: trace)
    monkeypatch.setattr(agent_type, "_execute_function_tool", original_execute)
    request_diagnostics.install_payload_latency_diagnostics()

    agent = SimpleNamespace()
    tool = {"spec": {"name": "demo"}, "function": {"type": "native"}}
    result = await agent_type._execute_function_tool(
        agent,
        tool,
        SimpleNamespace(tool_name="fallback", fail=False),
        None,
        None,
    )
    assert result.tool_result["result"] == "done"

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
    request_diagnostics.install_payload_latency_diagnostics()
    trace = _trace()
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
    request_diagnostics.install_payload_latency_diagnostics()
    trace = _trace()
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
    assert summary["first_model_request_characters"] == first.metrics["request_characters"]
    assert summary["first_model_request_approx_input_tokens"] == first.metrics[
        "approx_input_tokens"
    ]
    assert summary["provider_reported_model_cache_ratio"] == pytest.approx(0.3333)
    assert summary["slowest_phase"] == {"name": "provider", "duration_ms": 30}


def test_wrappers_fall_back_to_original_behavior_when_no_debug_trace(monkeypatch) -> None:
    agent_type = conversation_module.ExtendedOpenAIAgentEntity
    render_calls: list[str] = []

    def original_render(*_args, **_kwargs):
        render_calls.append("render")
        return "plain"

    monkeypatch.setattr(request_diagnostics, "_debug_trace", lambda: None)
    monkeypatch.setattr(conversation_module, "render_effective_prompt", original_render)
    monkeypatch.setattr(agent_type, "_get_exposed_entities", lambda _agent: ["entity"])
    monkeypatch.setattr(agent_type, "_get_function_tools", lambda _agent: ["tool"])
    request_diagnostics.install_payload_latency_diagnostics()

    agent = SimpleNamespace()
    assert conversation_module.render_effective_prompt() == "plain"
    assert agent_type._get_exposed_entities(agent) == ["entity"]
    assert agent_type._get_function_tools(agent) == ["tool"]
    assert render_calls == ["render"]
