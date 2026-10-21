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
    assert [value async for value in ordered_output_events(stream([done, done]))] == [
        done,
        done,
    ]
