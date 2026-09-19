"""Residual branch coverage for opt-in request debugging."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses import debug


class _ToDictFailure:
    __slots__ = ()

    def to_dict(self):
        raise RuntimeError("to_dict failed")

    def __repr__(self) -> str:
        return "<to-dict-fallback>"


class _FlakyVars:
    def __init__(self) -> None:
        self._dict_reads = 0
        self.value = "present"

    def __getattribute__(self, name: str):
        if name == "__dict__":
            reads = object.__getattribute__(self, "_dict_reads")
            object.__setattr__(self, "_dict_reads", reads + 1)
            if reads >= 1:
                raise RuntimeError("vars failed")
        return object.__getattribute__(self, name)

    def __repr__(self) -> str:
        return "<vars-fallback>"


def _request(name: str = "request") -> debug.DebugProviderRequest:
    return debug.DebugProviderRequest(
        request_id=name,
        api_surface="responses",
        started_at="now",
        started_offset_ms=0,
        request={},
        metrics={},
        _started_monotonic=debug.time.monotonic(),
    )


def _trace() -> debug.DebugTrace:
    return debug.DebugTrace(
        debug_id="debug-residual",
        entry_id="entry",
        subentry_id="agent",
        started_at="now",
        user_input={"text": "hello"},
        incoming_conversation_id=None,
        _started_monotonic=debug.time.monotonic(),
    )


def test_jsonable_adapter_failures_and_non_mapping_event_inputs() -> None:
    """Broken optional adapters fall through safely and event probes reject scalars."""
    assert debug._jsonable(_ToDictFailure()) == "<to-dict-fallback>"
    assert debug._jsonable(_FlakyVars()) == "<vars-fallback>"

    assert debug._event_has_text("not-an-event") is False
    assert debug._event_has_action(42) is False


def test_record_provider_failure_without_provider_request(monkeypatch) -> None:
    """A turn-level provider error is still recorded before any request object exists."""
    trace = _trace()
    error = RuntimeError("provider failed before request")
    monkeypatch.setattr(
        debug, "provider_error_metadata", lambda err: {"message": str(err)}
    )

    token = debug._ACTIVE_DEBUG_TRACE.set(trace)
    try:
        debug.record_current_provider_failure(error)
    finally:
        debug._ACTIVE_DEBUG_TRACE.reset(token)

    assert trace.error_type == "RuntimeError"
    assert trace.error == {"message": "provider failed before request"}
    assert trace.provider_requests == []


class _BareStream:
    def __init__(self) -> None:
        self.extra = "delegate-extra"

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _SyncCloseStream(_BareStream):
    def close(self):
        return "sync-closed"


async def test_debug_stream_optional_context_and_close_paths() -> None:
    """Stream wrapping tolerates delegates without context/close hooks and sync close."""
    bare_request = _request("bare")
    bare = debug._DebugAsyncStream(_BareStream(), bare_request)
    await bare.__aexit__(None, None, None)
    assert bare_request.successful is True
    assert bare.extra == "delegate-extra"

    no_close_request = _request("no-close")
    no_close = debug._DebugAsyncStream(_BareStream(), no_close_request)
    assert await no_close.close() is None
    assert no_close_request.successful is True

    sync_request = _request("sync-close")
    sync_close = debug._DebugAsyncStream(_SyncCloseStream(), sync_request)
    assert await sync_close.close() == "sync-closed"
    assert sync_request.successful is True


class _Endpoint:
    def __init__(self, result) -> None:
        self.result = result

    async def create(self, *_args, **_kwargs):
        return self.result


async def test_endpoint_proxy_leaves_stream_untouched_without_active_trace() -> None:
    """Instrumentation stays inert when capture is disabled, including streaming calls."""
    stream = _BareStream()
    proxy = debug._DebugEndpointProxy(_Endpoint(stream), "responses")

    assert await proxy.create(input="hello") is stream
