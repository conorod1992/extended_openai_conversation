"""Output admission failures retain provider accounting, never executable output."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    provider_output_budget as limits,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from tests.test_output_error_usage_recovery import (
    CORRECTION,
    FULL,
    Stream,
    malformed,
    usage_event,
)
from tests.test_provider_stream_terminals import _chat_chunk, _event
from tests.test_provider_tool_protocol import _chat_log, _entity
from tests.test_usage_accounting_recovery import FakeStorage


@pytest.mark.parametrize(
    "mode,limit",
    [
        ("chat", "arguments"),
        ("chat", "events"),
        ("chat", "text"),
        ("responses", "arguments"),
        ("responses", "buffer-size"),
        ("responses", "buffer-events"),
    ],
)
async def test_output_limit_failure_persists_trailing_usage_before_close(
    hass, monkeypatch, mode, limit
):
    if limit == "arguments":
        monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 8)
        rejected = [malformed(mode, '{"oversized":true}')]
    elif limit == "events":
        monkeypatch.setattr(limits, "MAX_OUTPUT_EVENTS", 1)
        rejected = [_chat_chunk(content="Accepted"), _chat_chunk(content="Rejected")]
    elif limit == "text":
        monkeypatch.setattr(limits, "MAX_OUTPUT_CHARACTERS", 8)
        rejected = [_chat_chunk(content="oversized output")]
    else:
        monkeypatch.setattr(
            limits,
            "MAX_BUFFERED_CHARACTERS"
            if limit == "buffer-size"
            else "MAX_BUFFERED_EVENTS",
            1,
        )
        rejected = [
            _event(
                "response.output_item.added",
                output_index=index,
                item=NS(type="message", id=f"item-{index}", content=[]),
            )
            for index in (0, 1)
        ]
        if limit == "buffer-events":
            rejected.append(
                _event("response.output_text.delta", output_index=1, delta="Rejected")
            )
    closed = []

    class Owned(Stream):
        async def close(self):
            closed.append(True)
            await super().close()

    # Even valid tool output after the rejection is accounting-only recovery.
    source = Owned(
        [
            *rejected,
            usage_event(mode, FULL),
            usage_event(mode, CORRECTION),
            malformed(mode, "{}"),
        ]
    )
    entity = _entity(hass, [source])
    create = entity.entry.runtime_data.chat.completions.create
    entity.entry.runtime_data.responses = NS(create=create)
    entity.subentry.data.update(
        api_mode="responses" if mode == "responses" else "chat_completions",
        function_tool_error_recovery=False,
    )
    entity._execute_function_tool = AsyncMock()
    stores = [FakeStorage(), FakeStorage(), FakeStorage()]
    manager = UsageManager(*stores)
    await manager.async_initialize()
    entity._usage = manager
    try:
        async with manager.async_run():
            with pytest.raises(ProviderStreamError, match="local safety limits"):
                await entity._async_handle_chat_log(_chat_log(hass), [], [])
    finally:
        await manager.async_prepare_for_deletion()
    fresh = UsageManager(*stores)
    await fresh.async_initialize()
    assert len(fresh.requests) == 1
    request = fresh.requests[0]
    assert (request.input_tokens, request.output_tokens, request.total_tokens) == (
        120,
        40,
        160,
    )
    assert (
        fresh.totals.input_tokens,
        fresh.totals.output_tokens,
        fresh.totals.total_tokens,
    ) == (120, 40, 160)
    assert not request.successful
    assert request.error_type == "ProviderStreamError"
    assert closed == [True]
    assert create.await_count == 1
    entity._execute_function_tool.assert_not_awaited()


@pytest.mark.parametrize("mode", ["chat", "responses"])
async def test_output_budget_recovery_cancellation_cannot_publish_late_usage(
    hass, monkeypatch, mode
):
    import asyncio

    reading, release, finished = (asyncio.Event() for _ in range(3))
    if mode == "chat":
        monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 8)
        rejected = [malformed(mode, '{"oversized":true}')]
    else:
        monkeypatch.setattr(limits, "MAX_BUFFERED_CHARACTERS", 1)
        rejected = [
            _event(
                "response.output_item.added",
                output_index=index,
                item=NS(type="message", id=f"item-{index}", content=[]),
            )
            for index in (0, 1)
        ]

    class Uncooperative(Stream):
        async def __anext__(self):
            if self._index < len(rejected):
                return await super().__anext__()
            if self._index > len(rejected):
                raise StopAsyncIteration
            self._index += 1
            reading.set()
            try:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return usage_event(mode, FULL)
            finally:
                finished.set()

    source = Uncooperative(rejected)
    entity = _entity(hass, [source])
    create = entity.entry.runtime_data.chat.completions.create
    entity.entry.runtime_data.responses = NS(create=create)
    entity.subentry.data.update(
        api_mode="responses" if mode == "responses" else "chat_completions",
        function_tool_error_recovery=False,
    )
    entity._execute_function_tool = AsyncMock()
    stores = [FakeStorage(), FakeStorage(), FakeStorage()]
    manager = UsageManager(*stores)
    await manager.async_initialize()
    entity._usage = manager

    async def request():
        async with manager.async_run():
            await entity._async_handle_chat_log(_chat_log(hass), [], [])

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(reading.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert source.closed
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await manager.async_prepare_for_deletion()
    await asyncio.wait_for(finished.wait(), 2)
    fresh = UsageManager(*stores)
    await fresh.async_initialize()
    assert len(fresh.requests) == 1
    assert fresh.totals.total_tokens == fresh.requests[0].total_tokens == 0
    entity._execute_function_tool.assert_not_awaited()
