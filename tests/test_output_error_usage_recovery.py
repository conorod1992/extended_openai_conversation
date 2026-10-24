"""Output interpretation failures preserve usage without further output effects."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening as hardening,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    ParseArgumentsFailed,
)
from custom_components.extended_openai_conversation_responses.non_streaming import (
    completed_chat_chunks,
    completed_responses_events,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    UsageManager,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_provider_stream_terminals import (
    FakeStream,
    _chat_chunk,
    _entity as parser_entity,
    _event,
)
from tests.test_provider_tool_protocol import _chat_log, _entity
from tests.test_usage_accounting_recovery import FakeStorage

FULL = {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
CORRECTION = {"output_tokens": 40, "total_tokens": 160}


class Stream(FakeStream):
    closed = False

    async def close(self):
        self.closed = True


def malformed(mode, arguments="{"):
    item = SimpleNamespace(
        type="function_call",
        id="item",
        call_id="call",
        name="lookup",
        arguments=arguments,
    )
    if mode == "responses":
        return _event("response.output_item.done", item=item)
    chunk = _chat_chunk(finish_reason="tool_calls")
    chunk.choices[0].delta.tool_calls = [
        SimpleNamespace(
            index=0,
            id="call",
            function=SimpleNamespace(name="lookup", arguments=arguments),
        )
    ]
    return chunk


def usage_event(mode, counts):
    return (
        SimpleNamespace(choices=[], usage=counts)
        if mode == "chat"
        else _event("response.usage", usage=counts)
    )


@pytest.mark.parametrize(
    "mode,problem,streaming",
    [
        ("chat", "stop", True),
        ("chat", "length", True),
        ("chat", "content_filter", True),
        ("chat", "refusal", True),
        ("chat", "malformed", True),
        ("responses", "malformed", True),
        ("responses", "malformed", False),
    ],
)
async def test_public_request_records_known_and_recovered_usage_once(
    hass, mode, problem, streaming
):
    if problem == "malformed":
        first = malformed(mode)
    else:
        first = _chat_chunk(
            finish_reason="content_filter" if problem == "refusal" else problem,
            refusal="Cannot help" if problem == "refusal" else None,
        )
    if streaming:
        source = Stream([first, usage_event(mode, FULL), usage_event(mode, CORRECTION)])
    else:
        source = SimpleNamespace(output=[first.item], status="completed", usage=FULL)
    entity = _entity(hass, [source])
    create = entity.entry.runtime_data.chat.completions.create
    entity.entry.runtime_data.responses = SimpleNamespace(create=create)
    entity.subentry.data.update(
        api_mode="responses" if mode == "responses" else "chat_completions",
        chat_model="gpt-5.5-pro" if not streaming else "gpt-4.1-mini",
        function_tool_error_recovery=False,
    )
    entity._execute_function_tool = AsyncMock()
    details = FakeStorage()
    manager = UsageManager(FakeStorage(), FakeStorage(), details)
    await manager.async_initialize()
    entity._usage = manager
    log = _chat_log(hass)
    success = problem in {"stop", "refusal"}
    try:
        async with manager.async_run():
            if success:
                await entity._async_handle_chat_log(log, [], [])
            else:
                with pytest.raises(HomeAssistantError):
                    await entity._async_handle_chat_log(log, [], [])
    finally:
        await manager.async_prepare_for_deletion()
    assert manager.totals.api_request_count == 1
    assert manager.totals.failed_request_count == (0 if success else 1)
    assert len(details.data["requests"]) == 1
    request = details.data["requests"][0]
    expected = (120, 40, 160) if streaming else (120, 30, 150)
    assert (
        request["input_tokens"],
        request["output_tokens"],
        request["total_tokens"],
    ) == expected
    assert (
        manager.totals.input_tokens,
        manager.totals.output_tokens,
        manager.totals.total_tokens,
    ) == expected
    if not success:
        assert (
            request["error_type"]
            == {
                "malformed": "ParseArgumentsFailed",
                "length": "TokenLengthExceededError",
                "content_filter": "HomeAssistantError",
            }[problem]
        )
    assert request["successful"] is success
    entity._execute_function_tool.assert_not_awaited()
    assert create.await_count == 1
    if streaming:
        assert source.closed


@pytest.mark.parametrize("mode", ["chat", "responses"])
@pytest.mark.parametrize("arguments", ["{", "{}"])
async def test_completed_adapter_captures_usage_before_output(mode, arguments):
    bad = malformed(mode, arguments)
    if mode == "responses":
        response = SimpleNamespace(output=[bad.item], status="completed", usage=FULL)
        adapter = completed_responses_events
        transform = parser_entity()._transform_responses_stream_events
    else:
        tool = bad.choices[0].delta.tool_calls[0]
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(tool_calls=[tool]),
                    finish_reason="tool_calls",
                )
            ],
            usage=FULL,
        )
        adapter = completed_chat_chunks
        transform = parser_entity()._transform_chat_stream
    usage = RequestUsage()
    source = adapter(response, usage)
    log = SimpleNamespace(async_trace=lambda _payload: None)
    if arguments == "{":
        with pytest.raises(ParseArgumentsFailed):
            _ = [delta async for delta in transform(log, source, usage)]
    else:
        _ = [delta async for delta in transform(log, source, usage)]
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        120,
        30,
        150,
    )
    await source.aclose()


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError, ValueError])
async def test_recovery_errors_cannot_replace_original_output_error(failure):
    class Broken(Stream):
        async def __anext__(self):
            if self._index:
                raise failure("recovery failed")
            return await super().__anext__()

    source = Broken([_chat_chunk(finish_reason="content_filter")])
    with pytest.raises(HomeAssistantError, match="content filter"):
        _ = [
            delta
            async for delta in parser_entity()._transform_chat_stream(
                SimpleNamespace(), source, RequestUsage()
            )
        ]


@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_recovery_deadline_excludes_late_usage(monkeypatch, suppress_cancel):
    monkeypatch.setattr(hardening, "_TRAILING_USAGE_TIMEOUT", 0.02)
    # Expire the recovery clock only after the iterator has actually entered its
    # stalled read. A heavily instrumented runner can otherwise exhaust 20 ms
    # before starting collect(), never exercising cancellation suppression.
    clock = [0.0]
    monkeypatch.setattr(
        hardening,
        "asyncio",
        SimpleNamespace(**(vars(asyncio) | {
            "get_running_loop": lambda: SimpleNamespace(time=lambda: clock[0])
        })),
    )
    release, finished = asyncio.Event(), asyncio.Event()

    class Stalled(Stream):
        async def __anext__(self):
            if self._index == 0:
                return await super().__anext__()
            clock[0] = 1.0
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    if not suppress_cancel:
                        raise
                    await release.wait()
                return usage_event("chat", FULL)
            finally:
                finished.set()

    source = Stalled([_chat_chunk(finish_reason="content_filter")])
    usage = RequestUsage()
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(HomeAssistantError, match="content filter"):
            _ = [
                delta
                async for delta in parser_entity()._transform_chat_stream(
                    SimpleNamespace(), source, usage
                )
            ]
        assert asyncio.get_running_loop().time() - started < 0.3
    finally:
        release.set()
        await asyncio.wait_for(finished.wait(), 0.3)
        await asyncio.sleep(0)
    assert usage.total_tokens == 0


async def test_cancellation_interrupts_recovery_immediately(monkeypatch):
    monkeypatch.setattr(hardening, "_TRAILING_USAGE_TIMEOUT", 60)
    reading, finished = asyncio.Event(), asyncio.Event()

    async def source():
        yield _chat_chunk(finish_reason="content_filter")
        reading.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    stream = source()

    async def consume():
        return [
            delta
            async for delta in parser_entity()._transform_chat_stream(
                SimpleNamespace(), stream, RequestUsage()
            )
        ]

    task = asyncio.create_task(consume())
    await reading.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.3)
    await asyncio.wait_for(finished.wait(), 0.3)
    await stream.aclose()


@pytest.mark.parametrize(
    "error", [asyncio.CancelledError, ConnectionError, TimeoutError]
)
async def test_transport_and_cancellation_do_not_start_recovery(monkeypatch, error):
    recovered = AsyncMock()
    from custom_components.extended_openai_conversation_responses import (
        entity as module,
    )

    monkeypatch.setattr(module, "async_recover_trailing_usage", recovered)

    async def source():
        raise error()
        yield

    with pytest.raises(error):
        _ = [
            delta
            async for delta in parser_entity()._transform_chat_stream(
                SimpleNamespace(), source(), RequestUsage()
            )
        ]
    recovered.assert_not_awaited()


async def test_recovery_cannot_publish_late_output_or_tools():
    source = Stream(
        [
            _chat_chunk(finish_reason="content_filter"),
            _chat_chunk(content="must not be published"),
            malformed("chat", "{}"),
            usage_event("chat", FULL),
        ]
    )
    usage, emitted, traces = RequestUsage(), [], []
    with pytest.raises(HomeAssistantError, match="content filter"):
        async for delta in parser_entity()._transform_chat_stream(
            SimpleNamespace(async_trace=traces.append), source, usage
        ):
            emitted.append(delta)
    assert emitted == [{"role": "assistant"}]
    assert traces == []
    assert usage.total_tokens == 150


@pytest.mark.parametrize("cancel", [False, True])
async def test_public_recovery_timeout_and_cancel_record_failure_once(
    hass, monkeypatch, cancel
):
    monkeypatch.setattr(hardening, "_TRAILING_USAGE_TIMEOUT", 60 if cancel else 0.02)
    reading, finished = asyncio.Event(), asyncio.Event()

    class Stalled(Stream):
        async def __anext__(self):
            if self._index == 0:
                return await super().__anext__()
            reading.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

    stream = Stalled([_chat_chunk(finish_reason="content_filter")])
    entity = _entity(hass, [stream])
    details = FakeStorage()
    manager = UsageManager(FakeStorage(), FakeStorage(), details)
    await manager.async_initialize()
    entity._usage = manager
    entity._execute_function_tool = AsyncMock()

    async def request():
        async with manager.async_run():
            await entity._async_handle_chat_log(_chat_log(hass), [], [])

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(reading.wait(), 1)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else HomeAssistantError):
            await asyncio.wait_for(task, 0.3)
        await asyncio.wait_for(finished.wait(), 0.3)
    finally:
        if not task.done():
            task.cancel()
        await manager.async_prepare_for_deletion()
    assert stream.closed
    assert manager.totals.api_request_count == 1
    assert manager.totals.failed_request_count == 1
    assert manager.totals.total_tokens == 0
    assert len(details.data["requests"]) == 1
    entity._execute_function_tool.assert_not_awaited()


async def test_recovery_has_an_event_limit(monkeypatch):
    monkeypatch.setattr(hardening, "_TRAILING_USAGE_EVENT_LIMIT", 3)
    stream = Stream([usage_event("chat", FULL)] * 4)
    usage = RequestUsage()
    await hardening.async_recover_trailing_usage(stream, usage)
    assert stream._index == 3
    assert usage.total_tokens == 150


@pytest.mark.parametrize("cancel", [False, True])
async def test_duplicate_id_recovery_remains_bounded_and_cancellable(
    monkeypatch, cancel
):
    monkeypatch.setattr(hardening, "_TRAILING_USAGE_TIMEOUT", 60 if cancel else 0.02)
    reading, finished = asyncio.Event(), asyncio.Event()

    class Stalled(Stream):
        async def __anext__(self):
            reading.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

    calls = [SimpleNamespace(id="duplicate"), SimpleNamespace(id="duplicate")]
    usage = RequestUsage()
    task = asyncio.create_task(
        hardening.async_validate_tool_call_ids_with_usage(calls, Stalled([]), usage)
    )
    try:
        await asyncio.wait_for(reading.wait(), 1)
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else HomeAssistantError):
            await asyncio.wait_for(task, 0.3)
        await asyncio.wait_for(finished.wait(), 0.3)
        assert usage.total_tokens == 0
    finally:
        if not task.done():
            task.cancel()
