"""Provider usage policy: snapshots, fallback, accounting and stream lifetime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    RequestTokenMeasurements,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    TokenLengthExceededError,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from tests.test_provider_stream_terminals import (
    FakeStream,
    _chat_chunk,
    _entity,
    _event,
)


def _estimated():
    return RequestTokenMeasurements(context_estimate=800)


def _reported(kind):
    return {
        "missing": None,
        "zero": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "full": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        "input": {"input_tokens": 120, "total_tokens": 120},
        "output": {"output_tokens": 30, "total_tokens": 30},
        "total": {"total_tokens": 150},
        "reasoning": {"output_tokens_details": {"reasoning_tokens": 17}},
        "cached": {"input_tokens_details": {"cached_tokens": 20}},
    }[kind]


def _provider_expected(kind):
    return {
        "missing": RequestUsage(),
        "zero": RequestUsage(),
        "full": RequestUsage(input_tokens=120, output_tokens=30, total_tokens=150),
        "input": RequestUsage(input_tokens=120, total_tokens=120),
        "output": RequestUsage(output_tokens=30, total_tokens=30),
        "total": RequestUsage(total_tokens=150),
        "reasoning": RequestUsage(
            reasoning_tokens=17, details={"output_reasoning_tokens": 17}
        ),
        "cached": RequestUsage(
            cached_input_tokens=20, details={"input_cached_tokens": 20}
        ),
    }[kind]


def _context_expected(kind):
    """Literal context count and active-fallback expectations, separate from usage."""
    return (120, None) if kind in {"full", "input"} else (800, 800)


def _assert_context(usage, kind):
    tokens, estimate = _context_expected(kind)
    assert usage.context_tokens == tokens
    assert usage.context_estimate == estimate


def _terminal(mode, raw, *, limited=False):
    if mode == "chat":
        return [
            _chat_chunk(
                content="Answer", finish_reason="length" if limited else "stop"
            ),
            SimpleNamespace(choices=[], usage=raw),
        ]
    return [
        _event(
            "response.completed" if not limited else "response.incomplete",
            response=SimpleNamespace(
                usage=raw,
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
        )
    ]


def _transform(mode, source, usage, traces):
    entity = _entity()
    log = SimpleNamespace(async_trace=traces.append)
    method = (
        entity._transform_chat_stream
        if mode == "chat"
        else entity._transform_responses_stream_events
    )
    return method(log, source, usage)


@pytest.mark.parametrize("mode", ["chat", "responses"])
@pytest.mark.parametrize("limited", [False, True], ids=["completed", "length"])
@pytest.mark.parametrize(
    "kind",
    ["missing", "zero", "full", "input", "output", "total", "reasoning", "cached"],
)
async def test_terminal_usage_context_accounting_and_diagnostics(mode, limited, kind):
    usage = _estimated()
    traces = []
    transformed = _transform(
        mode,
        FakeStream(_terminal(mode, _reported(kind), limited=limited)),
        usage,
        traces,
    )
    if limited:
        with pytest.raises(TokenLengthExceededError):
            _ = [delta async for delta in transformed]
    else:
        _ = [delta async for delta in transformed]

    _assert_context(usage, kind)
    assert usage.reported == _provider_expected(kind)
    expected_traces = (
        []
        if kind == "missing"
        else [
            {
                "stats": {
                    "input_tokens": _provider_expected(kind).input_tokens,
                    "output_tokens": _provider_expected(kind).output_tokens,
                }
            }
        ]
    )
    assert traces == expected_traces


@pytest.mark.parametrize("mode", ["chat", "responses"])
@pytest.mark.parametrize("last", ["full", "zero", "missing", "output"])
async def test_repeated_usage_updates_supplied_fields_without_addition(mode, last):
    usage = _estimated()
    traces = []
    first = (
        SimpleNamespace(choices=[], usage=_reported("full"))
        if mode == "chat"
        else _event("response.usage", usage=_reported("full"))
    )
    source = FakeStream([first, *_terminal(mode, _reported(last))])
    _ = [delta async for delta in _transform(mode, source, usage, traces)]
    # Partial reports retain earlier components; an explicit positive provider
    # total remains authoritative even when that provider counts only output.
    expected = _provider_expected("full")
    if last == "output":
        expected.total_tokens = 30
    assert usage.reported == expected
    _assert_context(usage, "full")
    assert traces[0] == {"stats": {"input_tokens": 120, "output_tokens": 30}}
    assert len(traces) == (1 if last == "missing" else 2)


@pytest.mark.parametrize("mode", ["chat", "responses"])
@pytest.mark.parametrize("ending", ["disconnect", "cancel", "close"])
async def test_interrupted_usage_retains_partial_provider_counts(mode, ending):
    usage = _estimated()
    traces = []
    reached = asyncio.Event()
    release = asyncio.Event()

    async def source():
        if mode == "chat":
            chunk = _chat_chunk(content="Partial")
            chunk.usage = _reported("output")
            yield chunk
        else:
            yield _event(
                "response.output_text.delta", delta="Partial", usage=_reported("output")
            )
        reached.set()
        if ending == "disconnect":
            raise ConnectionError("disconnected")
        await release.wait()

    transformed = _transform(mode, source(), usage, traces)
    if ending == "close":
        # Stop at the consumer-visible yield, without allowing wrapper repair.
        await anext(transformed)
        await transformed.aclose()
    else:

        async def consume():
            return [delta async for delta in transformed]

        task = asyncio.create_task(consume())
        await reached.wait()
        if ending == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if ending == "cancel" else ConnectionError
        ):
            await task
    _assert_context(usage, "output")
    assert usage.reported == _provider_expected("output")
    assert traces == [{"stats": {"input_tokens": 0, "output_tokens": 30}}]


@pytest.mark.parametrize(
    "kind",
    ["missing", "zero", "full", "input", "output", "total", "reasoning", "cached"],
)
async def test_failed_responses_event_preserves_context(kind):
    usage = _estimated()
    traces = []
    failed = _event(
        "response.failed",
        response=SimpleNamespace(
            usage=_reported(kind),
            error=SimpleNamespace(code="server_error", message="failed"),
            id="resp-failed",
        ),
    )
    with pytest.raises(ProviderStreamError):
        _ = [
            delta
            async for delta in _transform(
                "responses", FakeStream([failed]), usage, traces
            )
        ]
    _assert_context(usage, kind)
    assert usage.reported == _provider_expected(kind)
    assert len(traces) == (0 if kind in {"missing", "zero"} else 1)


@pytest.mark.parametrize("mode", ["chat", "responses"])
async def test_zero_nonterminal_usage_retains_previous_provider_snapshot(mode):
    usage = _estimated()
    traces = []
    if mode == "chat":
        first = _chat_chunk(content="First")
        first.usage = _reported("full")
        second = _chat_chunk(content="Second")
        second.usage = _reported("zero")
    else:
        first = _event("response.usage", usage=_reported("full"))
        second = _event("response.usage", usage=_reported("zero"))
    _ = [
        delta
        async for delta in _transform(
            mode, FakeStream([first, second, *_terminal(mode, None)]), usage, traces
        )
    ]
    assert usage.reported == _provider_expected("full")
    _assert_context(usage, "full")
    assert traces == [{"stats": {"input_tokens": 120, "output_tokens": 30}}]


async def test_chat_terminal_stats_wait_for_parser_admission():
    """Closing at the initial role does not newly publish terminal diagnostics."""
    usage = _estimated()
    traces = []
    transformed = _transform(
        "chat",
        FakeStream([SimpleNamespace(choices=[], usage=_reported("full"))]),
        usage,
        traces,
    )
    assert await anext(transformed) == {"role": "assistant"}
    await transformed.aclose()
    assert usage.reported == _provider_expected("full")
    _assert_context(usage, "full")
    assert traces == []


async def test_rejected_responses_terminal_does_not_publish_extra_stats():
    """Usage ownership does not change duplicate-terminal diagnostic admission."""
    usage = _estimated()
    traces = []
    source = FakeStream(
        [
            *_terminal("responses", _reported("full")),
            *_terminal("responses", _reported("output")),
        ]
    )
    with pytest.raises(ProviderStreamError):
        _ = [delta async for delta in _transform("responses", source, usage, traces)]
    assert traces == [{"stats": {"input_tokens": 120, "output_tokens": 30}}]
