"""Focused residual coverage for opt-in request debugging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import debug


@dataclass
class _Payload:
    value: str
    authorization: str


class _ModelDump:
    def model_dump(self, *, exclude_none: bool):
        assert exclude_none is True
        return {"value": "model", "api_key": "secret"}


class _AsDict:
    def as_dict(self):
        return {"value": "as-dict"}


class _ToDict:
    def to_dict(self):
        return {"value": "to-dict"}


class _AdapterFallback:
    def model_dump(self, **_kwargs):
        raise RuntimeError("model dump failed")

    def as_dict(self):
        raise RuntimeError("as dict failed")

    def to_dict(self):
        return {"value": "to-dict-after-errors"}


class _VarsPayload:
    def __init__(self) -> None:
        self.value = "vars"


class _ReprOnly:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<fallback>"


def _trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-1",
        entry_id="entry-1",
        subentry_id="agent-1",
        started_at="2026-09-13T10:00:00+00:00",
        user_input={"text": "hello"},
        incoming_conversation_id="conversation-1",
        _started_monotonic=debug.time.monotonic(),
    )


def test_jsonable_bounds_and_redacts_arbitrary_sdk_values(monkeypatch) -> None:
    monkeypatch.setattr(debug, "DEBUG_MAX_STRING_CHARACTERS", 4)

    value = {
        "Authorization": "Bearer secret",
        "x-api-key": "secret",
        "text": "abcdefgh",
        "binary": b"abc",
        "when": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "items": (1, {2, 3}),
        "data": _Payload("ok", "secret"),
        "model": _ModelDump(),
        "as_dict": _AsDict(),
        "to_dict": _ToDict(),
        "vars": _VarsPayload(),
    }

    serialized = debug._jsonable(value)

    assert serialized["Authorization"] == "<redacted credential>"
    assert serialized["x-api-key"] == "<redacted credential>"
    assert serialized["text"].startswith("abcd\n<truncated 4")
    assert serialized["binary"] == "<binary payload: 3 bytes>"
    assert serialized["when"].startswith("2026-01-02")
    assert serialized["data"]["authorization"] == "<redacted credential>"
    assert serialized["model"]["api_key"] == "<redacted credential>"
    assert serialized["as_dict"] == {"value": "as-dict"}
    assert serialized["to_dict"] == {"value": "to-dict"}
    assert serialized["vars"] == {"value": "vars"}
    assert sorted(serialized["items"][1]) == [2, 3]


def test_jsonable_adapter_failures_fall_through_without_breaking_capture() -> None:
    assert debug._jsonable(_AdapterFallback()) == {"value": "to-dict-after-errors"}
    assert debug._jsonable(_ReprOnly()) == "<fallback>"

    nested: object = "leaf"
    for _ in range(22):
        nested = [nested]
    assert "maximum debug serialization depth" in str(debug._jsonable(nested))


def test_usage_text_and_action_detection_support_provider_variants() -> None:
    assert debug._extract_usage("not-a-dict") is None
    assert debug._extract_usage({"response": {"usage": "bad"}}) is None
    assert debug._extract_usage(
        {
            "response": {
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 1},
                }
            }
        }
    ) == {
        "input_tokens": 3,
        "output_tokens": 4,
        "total_tokens": 7,
        "cached_input_tokens": 2,
        "reasoning_tokens": 1,
    }
    usage = debug._extract_usage(
        {"usage": {"input_tokens": -1, "output_tokens": "4", "total_tokens": 9}}
    )
    assert usage is not None
    assert usage["total_tokens"] == 9

    assert debug._event_has_text({"type": "response.output_text.delta", "delta": "x"})
    assert debug._event_has_text({"choices": [{"delta": {"content": "x"}}]})
    assert not debug._event_has_text({"choices": [None, {"delta": {}}]})
    assert debug._event_has_action({"type": "response.function_call_arguments.delta"})
    assert debug._event_has_action({"item": {"type": "web_search_call"}})
    assert not debug._event_has_action({"item": {"type": "message"}})


def test_provider_request_records_timings_usage_and_truncates(monkeypatch) -> None:
    request = debug.DebugProviderRequest(
        request_id="req-1",
        api_surface="responses",
        started_at="now",
        started_offset_ms=0,
        request={},
        metrics={},
        _started_monotonic=debug.time.monotonic(),
    )
    monkeypatch.setattr(debug, "DEBUG_MAX_EVENT_BYTES", 100)

    request.add_event(
        {
            "type": "response.output_text.delta",
            "delta": "hello",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }
    )
    assert request.first_event_ms is not None
    assert request.first_text_ms is not None
    assert request.usage["total_tokens"] == 5

    request.add_event(
        {"type": "response.function_call_arguments.delta", "payload": "x" * 200}
    )
    assert request.first_action_ms is not None
    assert request.response_events_truncated is True
    retained = list(request.response_events)
    request.add_event({"type": "ignored"})
    assert request.response_events == retained

    error = RuntimeError("provider failed")
    monkeypatch.setattr(debug, "provider_error_metadata", lambda err: {"message": str(err)})
    request.finish(successful=False, error=error)
    duration = request.duration_ms
    request.finish(successful=True)
    assert request.duration_ms == duration
    assert request.successful is True
    assert request.error_type is None
    assert request.as_dict()["request_id"] == "req-1"


def test_trace_request_metrics_summary_and_manager_lifecycle() -> None:
    trace = _trace()
    first = trace.start_provider_request(
        "responses", ("arg",), {"input": "hello", "tools": [{"type": "function"}]}
    )
    second = trace.start_provider_request("embeddings", (), {"input": "world"})
    first.usage = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    second.usage = {"input_tokens": 4, "cached_input_tokens": 2}
    first.first_text_ms = 12
    trace.continuity = {"resolved_conversation_id": "resolved", "resumed": True}

    summary = trace.summary()
    assert summary["provider_request_count"] == 2
    assert summary["first_text_ms"] == 12
    assert summary["input_tokens"] == 7
    assert summary["output_tokens"] == 2
    assert summary["total_tokens"] == 5
    assert summary["cached_input_tokens"] == 2
    assert summary["resolved_conversation_id"] == "resolved"
    assert first.metrics["tool_count"] == 1
    assert len(first.metrics["request_sha256"]) == 64

    manager = debug.DebugManager()
    with pytest.raises(ValueError, match="Debug run limit"):
        manager.configure(limit=7)
    manager.configure(enabled=True, limit=5)
    assert manager.status()["enabled"] is True
    assert manager.status()["limit"] == 5

    for index in range(6):
        run = manager.begin(
            entry_id="entry-1",
            subentry_id="agent-1",
            user_input={"index": index},
            incoming_conversation_id=None,
        )
        manager.finish(run, successful=True, result={"index": index})
    assert manager.status()["count"] == 5
    newest = manager.summaries()[0]["debug_id"]
    assert manager.get(newest) is not None
    assert manager.get("missing") is None
    assert manager.clear() == 5
    assert manager.clear() == 0


def test_debug_manager_is_scoped_per_agent() -> None:
    hass = SimpleNamespace(data={})
    first = debug.get_debug_manager(hass, "entry", "one")
    assert debug.get_debug_manager(hass, "entry", "one") is first
    assert debug.get_debug_manager(hass, "entry", "two") is not first


def test_record_current_provider_failure_is_optional_and_finishes_latest_request(
    monkeypatch,
) -> None:
    error = RuntimeError("boom")
    monkeypatch.setattr(debug, "provider_error_metadata", lambda err: {"message": str(err)})

    debug.record_current_provider_failure(error)

    trace = _trace()
    request = trace.start_provider_request("responses", (), {})
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        debug.record_current_provider_failure(error)
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)

    assert trace.error_type == "RuntimeError"
    assert trace.error == {"message": "boom"}
    assert request.successful is False
    assert request.error_type == "RuntimeError"


class _Stream:
    def __init__(self, events, *, error: BaseException | None = None) -> None:
        self.events = list(events)
        self.error = error
        self.closed = False
        self.exited = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.events:
            return self.events.pop(0)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        raise StopAsyncIteration

    async def __aexit__(self, _exc_type, _exc, _tb):
        self.exited = True

    async def close(self):
        self.closed = True
        return "closed"


async def test_debug_async_stream_records_success_error_context_and_close() -> None:
    request = debug.DebugProviderRequest(
        "req", "responses", "now", 0, {}, {}, _started_monotonic=debug.time.monotonic()
    )
    delegate = _Stream([{"type": "response.output_text.delta", "delta": "hi"}])
    wrapped = debug._DebugAsyncStream(delegate, request)
    assert wrapped.__aiter__() is wrapped
    assert await wrapped.__anext__() == {
        "type": "response.output_text.delta",
        "delta": "hi",
    }
    with pytest.raises(StopAsyncIteration):
        await wrapped.__anext__()
    assert request.successful is True

    failed_request = debug.DebugProviderRequest(
        "req2", "responses", "now", 0, {}, {}, _started_monotonic=debug.time.monotonic()
    )
    failed = debug._DebugAsyncStream(
        _Stream([], error=RuntimeError("stream failed")), failed_request
    )
    with pytest.raises(RuntimeError, match="stream failed"):
        await failed.__anext__()
    assert failed_request.successful is False

    context_request = debug.DebugProviderRequest(
        "req3", "responses", "now", 0, {}, {}, _started_monotonic=debug.time.monotonic()
    )
    context_delegate = _Stream([])
    context = debug._DebugAsyncStream(context_delegate, context_request)
    assert await context.__aenter__() is context
    await context.__aexit__(None, None, None)
    assert context_delegate.exited is True
    assert context_request.successful is True

    close_request = debug.DebugProviderRequest(
        "req4", "responses", "now", 0, {}, {}, _started_monotonic=debug.time.monotonic()
    )
    close_delegate = _Stream([])
    closing = debug._DebugAsyncStream(close_delegate, close_request)
    assert await closing.close() == "closed"
    assert close_delegate.closed is True
    assert close_request.successful is True


class _Endpoint:
    def __init__(self, result=None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.extra = "endpoint-extra"

    async def create(self, *_args, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.result


async def test_endpoint_proxy_is_transparent_without_trace_and_records_calls_with_trace() -> None:
    plain_result = {"id": "response", "usage": {"input_tokens": 1}}
    plain = debug._DebugEndpointProxy(_Endpoint(plain_result), "responses")
    assert await plain.create(model="gpt-test") is plain_result
    assert plain.extra == "endpoint-extra"

    trace = _trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        result = await debug._DebugEndpointProxy(
            _Endpoint(plain_result), "responses"
        ).create(model="gpt-test", input="hello")
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)
    assert result is plain_result
    assert len(trace.provider_requests) == 1
    request = trace.provider_requests[0]
    assert request.successful is True
    assert request.stream_open_ms is not None
    assert request.usage["input_tokens"] == 1

    failed_trace = _trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(failed_trace)
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            await debug._DebugEndpointProxy(
                _Endpoint(error=RuntimeError("provider failed")), "responses"
            ).create(input="hello")
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)
    assert failed_trace.provider_requests[0].successful is False

    stream_trace = _trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(stream_trace)
    try:
        stream = await debug._DebugEndpointProxy(
            _Endpoint(_Stream([{"type": "event"}])), "responses"
        ).create(input="hello")
        assert isinstance(stream, debug._DebugAsyncStream)
        assert await stream.__anext__() == {"type": "event"}
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)


def test_openai_client_proxy_wraps_supported_resources_and_delegates_other_attrs() -> None:
    delegate = SimpleNamespace(
        responses=_Endpoint(),
        chat=SimpleNamespace(completions=_Endpoint(), other="chat-extra"),
        embeddings=_Endpoint(),
        api_key="secret",
    )
    proxy = debug.DebugOpenAIClientProxy(delegate)

    assert isinstance(proxy.responses, debug._DebugEndpointProxy)
    assert isinstance(proxy.chat.completions, debug._DebugEndpointProxy)
    assert isinstance(proxy.embeddings, debug._DebugEndpointProxy)
    assert proxy.chat.other == "chat-extra"
    assert proxy.api_key == "secret"


async def test_install_debug_instrumentation_covers_lifecycle_and_phase_wrappers(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses.continuity import (
        ConversationContinuity,
    )
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    async def process(_self, _user_input):
        return {"response": "ok"}

    async def handle(_self, *_args, **_kwargs):
        return "handled"

    async def retrieve(_self, *_args, **_kwargs):
        return [{"id": "persistent"}]

    async def retrieve_temporary(_self, *_args, **_kwargs):
        return [{"id": "temporary"}, {"id": "two"}]

    def build_prompt(_self, *_args, **_kwargs):
        return "system prompt"

    resolve_result = SimpleNamespace(
        conversation_id="resolved",
        key="scope-key",
        resumed=True,
        history=[1, 2],
    )

    async def resolve(
        _self,
        _mode,
        _scope,
        _device_id,
        _incoming_conversation_id,
        _timeout_minutes,
        *,
        namespace=None,
    ):
        assert namespace == "ns"
        return resolve_result

    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_process", process)
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_handle_message", handle)
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_retrieve_memories", retrieve)
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_retrieve_temporary_memories",
        retrieve_temporary,
    )
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_build_system_prompt", build_prompt)
    monkeypatch.setattr(ConversationContinuity, "async_resolve", resolve)
    monkeypatch.setattr(debug, "_INSTRUMENTATION_INSTALLED", False)

    debug.install_debug_instrumentation()
    wrapped_process = ExtendedOpenAIAgentEntity._async_process
    wrapped_handle = ExtendedOpenAIAgentEntity._async_handle_message
    wrapped_retrieve = ExtendedOpenAIAgentEntity._async_retrieve_memories
    wrapped_temporary = ExtendedOpenAIAgentEntity._async_retrieve_temporary_memories
    wrapped_prompt = ExtendedOpenAIAgentEntity._build_system_prompt
    wrapped_resolve = ConversationContinuity.async_resolve

    debug.install_debug_instrumentation()
    assert ExtendedOpenAIAgentEntity._async_process is wrapped_process

    hass = SimpleNamespace(data={})
    manager = debug.get_debug_manager(hass, "entry", "agent")
    entity = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
        _usage=SimpleNamespace(current_run=lambda: SimpleNamespace(run_id="usage-run")),
    )
    user_input = SimpleNamespace(conversation_id="incoming", text="hello")

    assert await wrapped_process(entity, user_input) == {"response": "ok"}
    assert manager.status()["count"] == 0

    manager.configure(enabled=True)
    assert await wrapped_process(entity, user_input) == {"response": "ok"}
    assert manager.status()["count"] == 1
    assert manager.summaries()[0]["successful"] is True

    trace = _trace()
    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        assert await wrapped_handle(entity) == "handled"
        assert await wrapped_retrieve(entity) == [{"id": "persistent"}]
        assert await wrapped_temporary(entity) == [
            {"id": "temporary"},
            {"id": "two"},
        ]
        assert wrapped_prompt(entity) == "system prompt"
        continuity = SimpleNamespace()
        assert (
            await wrapped_resolve(
                continuity,
                "automatic",
                SimpleNamespace(scope_type="user"),
                "device-1",
                "incoming",
                30,
                namespace="ns",
            )
            is resolve_result
        )
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)

    assert trace.usage_run_id == "usage-run"
    assert trace.memory["persistent_count"] == 1
    assert trace.memory["temporary_count"] == 2
    assert trace.system_prompt == "system prompt"
    assert trace.prompt_metrics["characters"] == len("system prompt")
    assert trace.continuity["resolved_conversation_id"] == "resolved"
    assert trace.continuity["restored_history_items"] == 2
    assert "model_path_total" in trace.phases_ms
    assert "continuity_resolution" in trace.phases_ms


async def test_traced_process_records_failure_and_resets_context(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses.continuity import (
        ConversationContinuity,
    )
    from custom_components.extended_openai_conversation_responses.conversation import (
        ExtendedOpenAIAgentEntity,
    )

    async def fail(_self, _user_input):
        raise RuntimeError("pipeline failed")

    async def no_result(_self, *_args, **_kwargs):
        return []

    def empty_prompt(_self, *_args, **_kwargs):
        return ""

    async def no_resolve(
        _self,
        _mode,
        _scope,
        _device_id,
        _incoming_conversation_id,
        _timeout_minutes,
        *,
        namespace=None,
    ):
        return None

    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_process", fail)
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_handle_message", no_result)
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_async_retrieve_memories", no_result)
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_async_retrieve_temporary_memories",
        no_result,
    )
    monkeypatch.setattr(ExtendedOpenAIAgentEntity, "_build_system_prompt", empty_prompt)
    monkeypatch.setattr(ConversationContinuity, "async_resolve", no_resolve)
    monkeypatch.setattr(debug, "provider_error_metadata", lambda err: {"message": str(err)})
    monkeypatch.setattr(debug, "_INSTRUMENTATION_INSTALLED", False)

    debug.install_debug_instrumentation()
    wrapped = ExtendedOpenAIAgentEntity._async_process
    hass = SimpleNamespace(data={})
    manager = debug.get_debug_manager(hass, "entry", "agent")
    manager.configure(enabled=True)
    entity = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )

    with pytest.raises(RuntimeError, match="pipeline failed"):
        await wrapped(entity, SimpleNamespace(conversation_id=None))

    assert debug.current_debug_trace() is None
    summary = manager.summaries()[0]
    assert summary["successful"] is False
    assert summary["error_type"] == "RuntimeError"
