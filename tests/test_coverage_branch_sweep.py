"""Focused branch coverage for storage, schema, and debug lifecycle edges."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import debug
from custom_components.extended_openai_conversation_responses.entity import (
    _adjust_schema,
    _make_schema_nullable,
    _normalize_function_result,
    _normalize_url_citation,
    _schema_explicitly_allows_null,
)
from custom_components.extended_openai_conversation_responses.strict_store import (
    RecoveryGuardedStore,
    _async_settle_store_io,
    async_storage_lock,
)


@pytest.mark.asyncio
async def test_settle_store_io_propagates_native_task_cancellation() -> None:
    async def cancelled_operation() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _async_settle_store_io(cancelled_operation())


@pytest.mark.asyncio
async def test_settle_store_io_finishes_native_io_before_caller_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = False

    async def operation() -> str:
        nonlocal finished
        started.set()
        await release.wait()
        finished = True
        return "saved"

    task = asyncio.create_task(_async_settle_store_io(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished


@pytest.mark.asyncio
async def test_settle_store_io_preserves_native_exception_after_shield() -> None:
    async def operation() -> None:
        raise OSError("disk unavailable")

    with pytest.raises(OSError, match="disk unavailable"):
        await _async_settle_store_io(operation())


def test_recovery_guarded_store_availability_delegates_only_when_bound() -> None:
    store = RecoveryGuardedStore.__new__(RecoveryGuardedStore)
    store._recovery_gate = None
    store.require_available()
    assert store.recovery_pending is False

    gate = SimpleNamespace(recovery_required=True, require_available=Mock())
    store._recovery_gate = gate
    assert store.recovery_pending is True
    store.require_available()
    gate.require_available.assert_called_once_with()


@pytest.mark.asyncio
async def test_storage_lock_uses_recovery_gate_when_store_is_bound() -> None:
    entered: list[str] = []

    class Gate:
        recovery_required = False

        @asynccontextmanager
        async def shared(self, *, maintenance: bool):
            assert maintenance is True
            entered.append("gate")
            try:
                yield
            finally:
                entered.append("gate-exit")

    store = RecoveryGuardedStore.__new__(RecoveryGuardedStore)
    store._recovery_gate = Gate()
    lock = asyncio.Lock()

    async with async_storage_lock(store, lock):
        assert lock.locked()
        entered.append("body")

    assert entered == ["gate", "body", "gate-exit"]
    assert not lock.locked()


@pytest.mark.asyncio
async def test_storage_lock_falls_back_for_non_guarded_storage() -> None:
    lock = asyncio.Lock()
    async with async_storage_lock(SimpleNamespace(_store=object()), lock):
        assert lock.locked()
    assert not lock.locked()


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"nullable": True}, True),
        ({"type": "null"}, True),
        ({"type": ["string", "null"]}, True),
        ({"enum": ["a", None]}, True),
        ({"const": None}, True),
        ({"type": "string"}, False),
        ({"enum": ["a"]}, False),
        ({"const": "a"}, False),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, True),
        ({"anyOf": [{"type": "string"}, {"type": "number"}]}, False),
        ({"oneOf": [{"type": "string"}, {"type": "null"}]}, True),
        ({"oneOf": [{"type": "null"}, {"const": None}]}, False),
        ({"allOf": [{"type": "null"}, {"const": None}]}, True),
        ({"allOf": [{"type": "null"}, {"type": "string"}]}, False),
        ({"allOf": []}, False),
    ],
)
def test_schema_nullability_matrix(schema: dict, expected: bool) -> None:
    assert _schema_explicitly_allows_null(schema) is expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, {"type": ["string", "null"]}),
        ({"type": ["string", "number"]}, {"type": ["string", "number", "null"]}),
        (
            {"enum": ["a", "b"]},
            {"anyOf": [{"enum": ["a", "b"]}, {"type": "null"}]},
        ),
        (
            {"const": "a"},
            {"anyOf": [{"const": "a"}, {"type": "null"}]},
        ),
        (
            {"anyOf": [{"type": "string"}]},
            {
                "anyOf": [
                    {"anyOf": [{"type": "string"}]},
                    {"type": "null"},
                ]
            },
        ),
    ],
)
def test_make_schema_nullable_variants(schema: dict, expected: dict) -> None:
    _make_schema_nullable(schema)
    assert schema == expected


def test_make_schema_nullable_leaves_already_nullable_schema_unchanged() -> None:
    schema = {"type": ["string", "null"]}
    _make_schema_nullable(schema)
    assert schema == {"type": ["string", "null"]}


def test_adjust_schema_recurses_objects_arrays_and_compositions() -> None:
    schema = {
        "type": "object",
        "properties": {
            "already_required": {"type": "integer"},
            "optional": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"nested": {"type": "string"}},
                },
            },
            "choice": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {"flag": {"type": "boolean"}}},
                ]
            },
            "ignored": "not-a-schema",
        },
        "required": ["already_required"],
    }

    _adjust_schema(schema)

    assert schema["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["already_required", "optional", "choice"]
    optional = schema["properties"]["optional"]
    assert optional["type"] == ["array", "null"]
    nested = optional["items"]
    assert nested["strict"] is True
    assert nested["required"] == ["nested"]
    assert nested["properties"]["nested"]["type"] == ["string", "null"]
    choice = schema["properties"]["choice"]
    assert _schema_explicitly_allows_null(choice)
    object_variant = choice["anyOf"][1]
    assert object_variant["strict"] is True
    assert object_variant["required"] == ["flag"]


def test_adjust_schema_honours_legacy_nullable_keyword() -> None:
    schema = {"type": "string", "nullable": True}
    _adjust_schema(schema)
    assert schema == {"type": ["string", "null"]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"ok": True}, {"ok": True}),
        ([1, 2, 3], [1, 2, 3]),
        ({1, 2}, "{1, 2}"),
    ],
)
def test_normalize_function_result_serializable_and_fallback(value, expected) -> None:
    assert _normalize_function_result(value) == expected


def test_normalize_url_citation_accepts_objects_and_rejects_invalid_indexes() -> None:
    citation = SimpleNamespace(
        type="url_citation",
        start_index=2,
        end_index=8,
        title="Docs",
        url="https://example.invalid",
    )
    assert _normalize_url_citation(citation) == {
        "type": "url_citation",
        "start_index": 2,
        "end_index": 8,
        "title": "Docs",
        "url": "https://example.invalid",
    }
    assert _normalize_url_citation({"type": "other"}) is None
    assert (
        _normalize_url_citation(
            {"type": "url_citation", "start_index": "2", "end_index": 8}
        )
        is None
    )


class _Request:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.finished: list[tuple[bool, BaseException | None]] = []

    def add_event(self, event: object) -> None:
        self.events.append(event)

    def finish(
        self, *, successful: bool, error: BaseException | None = None
    ) -> None:
        self.finished.append((successful, error))


class _AsyncIterator:
    def __init__(self, events: list[object], error: BaseException | None = None) -> None:
        self.events = list(events)
        self.error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.events:
            return self.events.pop(0)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_debug_stream_records_events_and_finishes_on_exhaustion() -> None:
    request = _Request()
    stream = debug._DebugAsyncStream(_AsyncIterator(["one", "two"]), request)

    assert [event async for event in stream] == ["one", "two"]
    assert request.events == ["one", "two"]
    assert request.finished == [(True, None)]


@pytest.mark.asyncio
async def test_debug_stream_records_iteration_failure() -> None:
    request = _Request()
    error = RuntimeError("stream failed")
    stream = debug._DebugAsyncStream(_AsyncIterator([], error), request)

    with pytest.raises(RuntimeError, match="stream failed"):
        await stream.__anext__()
    assert request.finished == [(False, error)]


@pytest.mark.asyncio
@pytest.mark.parametrize("close_kind", ["missing", "sync", "async"])
async def test_debug_stream_close_surfaces_all_delegate_close_shapes(close_kind: str) -> None:
    request = _Request()
    delegate = _AsyncIterator([])

    if close_kind == "sync":
        delegate.close = Mock(return_value="closed")
    elif close_kind == "async":
        delegate.close = AsyncMock(return_value="closed")

    stream = debug._DebugAsyncStream(delegate, request)
    result = await stream.close()

    assert result is None if close_kind == "missing" else result == "closed"
    assert request.finished == [(True, None)]


@pytest.mark.asyncio
async def test_debug_stream_context_exit_delegates_and_records_failure() -> None:
    request = _Request()
    delegate = _AsyncIterator([])
    delegate.__aexit__ = AsyncMock()
    stream = debug._DebugAsyncStream(delegate, request)
    error = ValueError("consumer failed")

    await stream.__aexit__(ValueError, error, None)

    delegate.__aexit__.assert_awaited_once_with(ValueError, error, None)
    assert request.finished == [(False, error)]


@pytest.mark.asyncio
async def test_debug_endpoint_without_trace_is_transparent(monkeypatch) -> None:
    delegate = SimpleNamespace(create=AsyncMock(return_value={"ok": True}), marker=42)
    monkeypatch.setattr(debug, "current_debug_trace", lambda: None)
    proxy = debug._DebugEndpointProxy(delegate, "responses")

    assert await proxy.create(model="test") == {"ok": True}
    assert proxy.marker == 42
    delegate.create.assert_awaited_once_with(model="test")
