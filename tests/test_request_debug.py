"""Tests for opt-in request debug capture."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.debug import (
    _ACTIVE_DEBUG_TRACE,
    DebugManager,
    DebugOpenAIClientProxy,
    DebugProviderRequest,
    _jsonable,
    conversation_debug_trace,
    current_debug_trace,
    model_path_timing,
    record_continuity_resolution,
    record_current_provider_failure,
    record_memory_retrieval,
    record_system_prompt,
)
from homeassistant.helpers.template import Template


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


def test_nested_agent_prompt_cannot_overwrite_callers_debug_trace() -> None:
    manager = DebugManager()
    manager.configure(enabled=True, limit=5)
    trace = manager.begin(
        entry_id="entry-a",
        subentry_id="agent-a",
        user_input={"text": "caller"},
        incoming_conversation_id=None,
    )
    token = _ACTIVE_DEBUG_TRACE.set(trace)
    try:
        child = SimpleNamespace(
            entry=SimpleNamespace(entry_id="entry-b"),
            subentry=SimpleNamespace(subentry_id="agent-b"),
        )
        record_system_prompt(child, "child prompt", 0.0)
        assert trace.system_prompt is None
        caller = SimpleNamespace(
            entry=SimpleNamespace(entry_id="entry-a"),
            subentry=SimpleNamespace(subentry_id="agent-a"),
        )
        record_system_prompt(caller, "caller prompt", 0.0)
        assert trace.system_prompt == "caller prompt"
    finally:
        _ACTIVE_DEBUG_TRACE.reset(token)


async def test_disabled_nested_agent_establishes_no_capture_boundary() -> None:
    parent_manager = DebugManager()
    parent_manager.configure(enabled=True)
    parent = parent_manager.begin(
        entry_id="entry-a",
        subentry_id="agent-a",
        user_input={},
        incoming_conversation_id=None,
    )
    parent.system_prompt = "parent prompt"
    parent.usage_run_id = "parent-run"
    parent.continuity = {"resolved_conversation_id": "parent-conversation"}
    token = _ACTIVE_DEBUG_TRACE.set(parent)
    child = SimpleNamespace(
        hass=SimpleNamespace(data={}),
        entry=SimpleNamespace(entry_id="entry-b"),
        subentry=SimpleNamespace(subentry_id="agent-b"),
    )
    try:
        with conversation_debug_trace(
            child, SimpleNamespace(conversation_id="child")
        ) as trace:
            assert trace is None
            assert current_debug_trace() is None
            record_system_prompt(child, "child prompt", 0.0)
            record_memory_retrieval("persistent", 0.0, ["child secret memory"])
            record_continuity_resolution(
                SimpleNamespace(
                    conversation_id="child-conversation",
                    key="child-key",
                    resumed=True,
                    history=[],
                ),
                0.0,
                "new",
                {},
                None,
                "child",
                5,
                None,
            )
            usage = SimpleNamespace(
                current_run=lambda: SimpleNamespace(run_id="child-run")
            )
            with model_path_timing(SimpleNamespace(_usage=usage)):
                pass

            class Endpoint:
                async def create(self, **_kwargs):
                    return {"usage": {"input_tokens": 1, "output_tokens": 1}}

            endpoint = Endpoint()
            proxy = DebugOpenAIClientProxy(
                SimpleNamespace(
                    responses=endpoint,
                    chat=SimpleNamespace(completions=endpoint),
                    embeddings=endpoint,
                )
            )
            await proxy.responses.create(model="child")
            record_current_provider_failure(RuntimeError("child provider failure"))
        assert current_debug_trace() is parent
    finally:
        _ACTIVE_DEBUG_TRACE.reset(token)
    assert parent.system_prompt == "parent prompt"
    assert parent.usage_run_id == "parent-run"
    assert parent.continuity == {"resolved_conversation_id": "parent-conversation"}
    assert parent.memory == {}
    assert parent.phases_ms == {}
    assert parent.error_type is None
    assert parent.provider_requests == []


async def test_enabled_nested_agent_records_to_its_own_trace() -> None:
    parent = DebugManager()
    parent.configure(enabled=True)
    child_hass = SimpleNamespace(data={})
    from custom_components.extended_openai_conversation_responses.debug import (
        get_debug_manager,
    )

    child_manager = get_debug_manager(child_hass, "entry-b", "agent-b")
    child_manager.configure(enabled=True)
    root = parent.begin(
        entry_id="entry-a",
        subentry_id="agent-a",
        user_input={},
        incoming_conversation_id=None,
    )
    token = _ACTIVE_DEBUG_TRACE.set(root)
    child = SimpleNamespace(
        hass=child_hass,
        entry=SimpleNamespace(entry_id="entry-b"),
        subentry=SimpleNamespace(subentry_id="agent-b"),
    )
    try:
        with conversation_debug_trace(
            child, SimpleNamespace(conversation_id="child")
        ) as trace:
            assert trace is not None and trace is not root
            assert current_debug_trace() is trace
            record_memory_retrieval("persistent", 0.0, ["child memory"])
        assert current_debug_trace() is root
    finally:
        _ACTIVE_DEBUG_TRACE.reset(token)
    assert root.memory == {}
    assert child_manager.status()["count"] == 1


@dataclass
class _TemplateResult:
    template: Template
    authorization: str = "Bearer secret"


def test_debug_serialization_does_not_deepcopy_template(hass) -> None:
    """Dataclass/debug serialization must not traverse Template.hass or leak secrets."""
    result = _TemplateResult(Template("{{ value }}", hass))
    expected = {
        "template": "{{ value }}",
        "authorization": "<redacted credential>",
    }

    assert _jsonable(result) == expected

    request = DebugProviderRequest(
        request_id="request",
        api_surface="responses",
        started_at="now",
        started_offset_ms=0,
        request={},
        metrics={},
        response_events=[result],
    )
    assert request.as_dict()["response_events"] == [expected]


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


@pytest.mark.parametrize("surface,event", [
    ("chat_completions", {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"effect"}}]}}]}),
    ("responses", {"type":"response.output_item.added", "item":{"type":"function_call"}}),
])
def test_first_action_timing_covers_both_provider_formats(monkeypatch, surface, event):
    from custom_components.extended_openai_conversation_responses import debug
    request=DebugProviderRequest(request_id="timing",api_surface=surface,started_at="now",started_offset_ms=0,request={},metrics={})
    request._started_monotonic=10.0
    monkeypatch.setattr(debug.time,"monotonic", lambda:10.125)
    request.add_event({"choices":[{"delta":{"tool_calls":[]}}, {}]})
    assert request.first_action_ms is None
    request.add_event(event)
    assert request.first_action_ms==125
    monkeypatch.setattr(debug.time,"monotonic", lambda:10.250)
    request.add_event(event)
    assert request.first_action_ms==125
    assert request.response_events[-1]==event
