"""Remaining resilience coverage for context usage hardening."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening as hardening,
    entity as entity_module,
    request as request_module,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)


def _state(*, factory=None):
    return hardening._EstimateState(
        entity=SimpleNamespace(
            entry=SimpleNamespace(data={}),
            _provider_tool_allowed=lambda _name: True,
        ),
        function_tools=[{"spec": {"name": "base"}}],
        function_tools_factory=factory,
        conditional_continue=False,
        options={},
    )


def test_request_tools_falls_back_when_dynamic_factory_raises(monkeypatch) -> None:
    def broken_factory():
        raise RuntimeError("group refresh failed")

    monkeypatch.setattr(
        request_module, "format_function_tools", lambda tools, _api_mode: tools
    )
    monkeypatch.setattr(
        request_module,
        "build_provider_request_snapshot",
        lambda _options, _entry_data: SimpleNamespace(provider_tools=[]),
    )

    tools = hardening._request_tools(_state(factory=broken_factory), "responses")

    assert tools == [{"spec": {"name": "base"}}]


def test_request_tools_falls_back_when_provider_snapshot_raises(monkeypatch) -> None:
    def broken_snapshot(_options, _entry_data):
        raise RuntimeError("provider snapshot unavailable")

    monkeypatch.setattr(
        request_module, "format_function_tools", lambda tools, _api_mode: tools
    )
    monkeypatch.setattr(
        request_module, "build_provider_request_snapshot", broken_snapshot
    )

    tools = hardening._request_tools(_state(), "chat_completions")

    assert tools == [{"spec": {"name": "base"}}]


@pytest.mark.asyncio
async def test_chat_stream_traces_usage_attached_to_normal_choice(
    monkeypatch,
) -> None:
    entity_type = entity_module.ExtendedOpenAIBaseLLMEntity

    async def fake_handle(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def passthrough_transform(
        _entity: Any,
        _chat_log: Any,
        result: Any,
        _request_usage: RequestUsage | None = None,
    ):
        async for item in result:
            yield item

    async def fake_record_request(*_args: Any, **_kwargs: Any) -> None:
        return None

    # Install the wrapper around controlled originals. monkeypatch restores the real
    # class methods after the test, so this does not leak process-global composition.
    monkeypatch.setattr(entity_type, "_async_handle_chat_log", fake_handle)
    monkeypatch.setattr(entity_type, "_transform_chat_stream", passthrough_transform)
    monkeypatch.setattr(entity_type, "_transform_responses_stream", passthrough_transform)
    monkeypatch.setattr(UsageManager, "async_record_request", fake_record_request)
    monkeypatch.setattr(hardening, "_INSTALLED", False)
    hardening.install_context_usage_hardening()

    usage = RequestUsage()
    traces: list[dict[str, Any]] = []
    chat_log = SimpleNamespace(
        content=[],
        async_trace=lambda payload: traces.append(payload),
    )
    chunk = SimpleNamespace(
        usage={
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
        },
        choices=[SimpleNamespace()],
    )

    async def stream():
        yield chunk

    transformed = entity_type._transform_chat_stream(
        SimpleNamespace(), chat_log, stream(), usage
    )
    items = [item async for item in transformed]

    assert items == [chunk]
    assert usage.input_tokens == 12
    assert usage.output_tokens == 3
    assert usage.total_tokens == 15
    assert traces == [{"stats": {"input_tokens": 12, "output_tokens": 3}}]
