"""Tests for opt-in request debug capture."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.debug import (
    _ACTIVE_DEBUG_TRACE,
    DebugManager,
    DebugOpenAIClientProxy,
)


def _trace(manager: DebugManager, index: int = 0):
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={"text": f"request {index}"},
        incoming_conversation_id=f"incoming-{index}",
    )
    trace.system_prompt = f"full prompt {index}"
    trace.continuity = {
        "resolved_conversation_id": f"resolved-{index}",
        "resumed": bool(index % 2),
    }
    return trace


def test_debug_manager_is_bounded_and_returns_complete_trace() -> None:
    manager = DebugManager()
    manager.configure(enabled=True, limit=5)

    for index in range(6):
        trace = _trace(manager, index)
        manager.finish(trace, successful=True, result={"speech": "done"})

    summaries = manager.summaries()
    assert len(summaries) == 5
    assert summaries[0]["resolved_conversation_id"] == "resolved-5"
    assert manager.get(summaries[0]["debug_id"])["system_prompt"] == "full prompt 5"
    assert manager.get(summaries[-1]["debug_id"])["incoming_conversation_id"] == (
        "incoming-1"
    )


def test_debug_manager_can_resize_and_clear() -> None:
    manager = DebugManager()
    manager.configure(enabled=True, limit=10)
    for index in range(7):
        trace = _trace(manager, index)
        manager.finish(trace, successful=True)

    manager.configure(limit=5)
    assert manager.status()["count"] == 5
    assert manager.clear() == 5
    assert manager.status()["count"] == 0

    with pytest.raises(ValueError, match="Debug run limit"):
        manager.configure(limit=3)


class _FakeStream:
    def __init__(self, events):
        self._iterator = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as err:
            raise StopAsyncIteration from err


class _FakeEndpoint:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def create(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class _FakeClient:
    def __init__(self, events):
        self.responses = _FakeEndpoint(_FakeStream(events))
        self.chat = SimpleNamespace(completions=_FakeEndpoint(_FakeStream([])))
        self.embeddings = _FakeEndpoint(SimpleNamespace(data=[]))


async def test_client_proxy_captures_exact_request_timing_and_usage() -> None:
    events = [
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "Yes"},
        {
            "type": "response.completed",
            "response": {
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 10,
                    "total_tokens": 1010,
                    "input_tokens_details": {"cached_tokens": 900},
                    "output_tokens_details": {"reasoning_tokens": 4},
                }
            },
        },
    ]
    manager = DebugManager()
    manager.configure(enabled=True)
    trace = _trace(manager)
    proxy = DebugOpenAIClientProxy(_FakeClient(events))
    token = _ACTIVE_DEBUG_TRACE.set(trace)
    try:
        stream = await proxy.responses.create(
            model="gpt-test",
            stream=True,
            input=[{"role": "system", "content": "complete prompt"}],
            tools=[{"type": "function", "name": "light_status"}],
            service_tier="default",
        )
        assert [event async for event in stream] == events
    finally:
        _ACTIVE_DEBUG_TRACE.reset(token)

    assert len(trace.provider_requests) == 1
    request = trace.provider_requests[0]
    assert request.request["kwargs"]["input"][0]["content"] == "complete prompt"
    assert request.request["kwargs"]["service_tier"] == "default"
    assert request.metrics["tool_count"] == 1
    assert request.first_event_ms is not None
    assert request.first_text_ms is not None
    assert request.successful is True
    assert request.usage == {
        "input_tokens": 1000,
        "output_tokens": 10,
        "total_tokens": 1010,
        "cached_input_tokens": 900,
        "reasoning_tokens": 4,
    }


async def test_client_proxy_redacts_explicit_auth_headers() -> None:
    manager = DebugManager()
    manager.configure(enabled=True)
    trace = _trace(manager)
    proxy = DebugOpenAIClientProxy(_FakeClient([]))
    token = _ACTIVE_DEBUG_TRACE.set(trace)
    try:
        stream = await proxy.responses.create(
            model="gpt-test",
            stream=True,
            input=[],
            extra_headers={"Authorization": "Bearer secret", "X-Test": "visible"},
        )
        assert [event async for event in stream] == []
    finally:
        _ACTIVE_DEBUG_TRACE.reset(token)

    headers = trace.provider_requests[0].request["kwargs"]["extra_headers"]
    assert headers["Authorization"] == "<redacted credential>"
    assert headers["X-Test"] == "visible"
