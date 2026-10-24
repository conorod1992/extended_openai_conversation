"""Sequential HA assembly preserves indexed Responses output ownership."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from custom_components.extended_openai_conversation_responses.responses_stream_order import (
    ordered_output_events,
)


def event(kind, **values):
    if kind in {"response.output_item.added", "response.output_item.done"}:
        values.setdefault(
            "item",
            SimpleNamespace(type="message", id=f"msg_{values.get('output_index', 0)}"),
        )
    return SimpleNamespace(type=kind, **values)


async def stream(events):
    for value in events:
        yield value


async def test_leading_item_still_streams_before_completion():
    first = event("response.output_item.added", output_index=0)
    text = event("response.output_text.delta", output_index=0, delta="Live")
    ordered = ordered_output_events(stream([first, text]))
    assert await anext(ordered) is first
    assert await anext(ordered) is text
    await ordered.aclose()


async def test_partial_later_item_flushes_then_continues_live():
    events = [
        event("response.output_item.added", output_index=0),
        event("response.output_item.added", output_index=1),
        event("response.output_text.delta", output_index=1, delta="first"),
        event("response.output_item.done", output_index=0),
        event("response.output_text.delta", output_index=1, delta="second"),
        event("response.output_item.done", output_index=1),
    ]
    result = [value async for value in ordered_output_events(stream(events))]
    assert result == [events[i] for i in [0, 3, 1, 2, 4, 5]]


async def test_success_cannot_hide_unfinished_buffered_output():
    with pytest.raises(ProviderStreamError, match="unfinished output item"):
        _ = [
            value
            async for value in ordered_output_events(
                stream(
                    [
                        event("response.output_item.added", output_index=1),
                        event("response.completed"),
                    ]
                )
            )
        ]


@pytest.mark.parametrize("failure", ["response.incomplete", "response.failed", "error"])
async def test_failure_is_not_delayed_by_buffered_output(failure):
    terminal = event(failure)
    result = [
        value
        async for value in ordered_output_events(
            stream([event("response.output_item.added", output_index=1), terminal])
        )
    ]
    assert result == [terminal]


async def test_repeated_completion_reaches_existing_validation():
    done = event("response.output_item.done", output_index=0)
    with pytest.raises(ProviderStreamError, match="repeated completed output item"):
        _ = [value async for value in ordered_output_events(stream([done, done]))]


async def test_closing_output_adapter_closes_all_owned_layers():
    closed = []

    async def source():
        try:
            yield event("response.output_item.added", output_index=0)
            yield event("response.output_item.done", output_index=0)
        finally:
            closed.append(True)

    ordered = ordered_output_events(source())
    await anext(ordered)
    await ordered.aclose()
    assert closed == [True]


@pytest.mark.parametrize("outcome", ["close", "error", "cancel"])
async def test_responses_parser_closes_source_on_every_exit(outcome):
    import asyncio
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    entered = asyncio.Event()
    closed = []

    class Source:
        async def __aiter__(self):
            try:
                yield event("response.output_item.added", output_index=0)
                entered.set()
                if outcome == "error":
                    raise RuntimeError("source failed")
                await asyncio.Event().wait()
            finally:
                closed.append(True)

    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(data={})
    parsed = entity._transform_responses_stream(SimpleNamespace(content=[]), Source())
    assert await anext(parsed) == {"role": "assistant"}
    if outcome == "close":
        await parsed.aclose()
    elif outcome == "error":
        with pytest.raises(RuntimeError, match="source failed"):
            await anext(parsed)
    else:
        task = asyncio.create_task(anext(parsed))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert closed == [True]


async def test_protocol_recovery_runs_before_owned_source_is_closed():
    order = []

    async def source():
        try:
            yield event("response.output_item.done", output_index=0)
            yield event("response.output_item.done", output_index=0)
        finally:
            order.append("closed")

    async def recover():
        order.append("recover")

    with pytest.raises(ProviderStreamError, match="repeated completed"):
        _ = [
            item
            async for item in ordered_output_events(source(), recover_usage=recover)
        ]
    assert order == ["recover", "closed"]
