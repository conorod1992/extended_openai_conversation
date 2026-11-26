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


@pytest.mark.parametrize("fields", [{}, {"output_index": 0}], ids=["legacy", "indexed"])
async def test_argument_snapshot_is_authoritative_without_preceding_deltas(fields):
    arguments = event(
        "response.function_call_arguments.done", arguments='{"value":1}', **fields
    )
    item = SimpleNamespace(
        type="function_call", call_id="call-1", name="test", arguments='{"value":2}'
    )
    done = event("response.output_item.done", item=item, **fields)
    with pytest.raises(ProviderStreamError, match="conflicting tool arguments"):
        _ = [frame async for frame in ordered_output_events(stream([arguments, done]))]
    # Providers that send arguments only in the final item are still supported.
    assert done in [frame async for frame in ordered_output_events(stream([done]))]


@pytest.mark.parametrize("mode", ["indexed", "legacy", "mixed", "mixed-no-id"])
@pytest.mark.parametrize("matches", [False, True])
async def test_late_argument_snapshot_must_agree_with_completed_item(mode, matches):
    item_fields = {} if mode == "legacy" else {"output_index": 0}
    argument_fields = {
        "indexed": {"output_index": 0},
        "legacy": {"item_id": "item-1"},
        "mixed": {"item_id": "item-1"},
        "mixed-no-id": {},
    }[mode]
    item = SimpleNamespace(
        type="function_call",
        id="item-1",
        call_id="call-1",
        name="test",
        arguments='{"value":1}',
    )
    frames = [
        event("response.output_item.added", item=item, **item_fields),
        event("response.output_item.done", item=item, **item_fields),
        event(
            "response.function_call_arguments.done",
            arguments=item.arguments if matches else "{}",
            **argument_fields,
        ),
        event("response.completed", response=SimpleNamespace(output=[item])),
    ]
    if matches:
        assert [
            frame async for frame in ordered_output_events(stream(frames))
        ] == frames
    else:
        with pytest.raises(ProviderStreamError, match="conflicting tool arguments"):
            _ = [frame async for frame in ordered_output_events(stream(frames))]


@pytest.mark.parametrize("matches", [False, True])
async def test_unindexed_snapshot_before_indexed_item_is_bound_by_identity(matches):
    item = SimpleNamespace(
        type="function_call", id="item-1", call_id="call-1", name="test", arguments="{}"
    )
    frames = [
        event(
            "response.function_call_arguments.done",
            item_id=item.id,
            arguments=item.arguments if matches else '{"value":1}',
        ),
        event("response.output_item.done", item=item, output_index=0),
        event("response.completed", response=SimpleNamespace(output=[item])),
    ]
    if matches:
        parsed = [frame async for frame in ordered_output_events(stream(frames))]
        assert parsed[-2:] == frames[-2:]
    else:
        with pytest.raises(ProviderStreamError, match="conflicting tool arguments"):
            _ = [frame async for frame in ordered_output_events(stream(frames))]


async def test_unindexed_snapshot_is_rejected_when_multiple_calls_could_own_it():
    frames = [
        event(
            "response.output_item.done",
            output_index=index,
            item=SimpleNamespace(
                type="function_call",
                id=f"item-{index}",
                call_id=f"call-{index}",
                name="test",
                arguments="{}",
            ),
        )
        for index in range(2)
    ]
    frames.append(event("response.function_call_arguments.done", arguments="{}"))
    with pytest.raises(ProviderStreamError, match="ambiguous unindexed tool arguments"):
        _ = [frame async for frame in ordered_output_events(stream(frames))]


@pytest.mark.parametrize(
    "status,error", [("failed", None), ("completed", {"message": "failure"})]
)
async def test_completed_event_rejects_failure_in_embedded_response(status, error):
    done = event(
        "response.completed", response=SimpleNamespace(status=status, error=error)
    )
    with pytest.raises(ProviderStreamError):
        _ = [frame async for frame in ordered_output_events(stream([done]))]


@pytest.mark.parametrize(
    "kind,values",
    [
        ("response.function_call_arguments.delta", {"delta": 42}),
        ("response.function_call_arguments.done", {"arguments": None}),
        ("response.output_text.delta", {"delta": 42}),
        ("response.output_text.done", {"text": ["not text"]}),
    ],
)
async def test_invalid_string_fields_raise_structured_provider_error(kind, values):
    bad = event(kind, output_index=0, **values)
    with pytest.raises(ProviderStreamError, match="must be a string"):
        _ = [frame async for frame in ordered_output_events(stream([bad]))]


async def test_invalid_text_in_completed_message_raises_structured_provider_error():
    item = SimpleNamespace(
        type="message", content=[SimpleNamespace(type="output_text", text=42)]
    )
    frame = event("response.output_item.done", output_index=0, item=item)
    with pytest.raises(ProviderStreamError, match="output text must be a string"):
        _ = [value async for value in ordered_output_events(stream([frame]))]


@pytest.mark.parametrize("index", [[], -1, True, "0"])
async def test_invalid_content_index_raises_structured_provider_error(index):
    frame = event(
        "response.output_text.delta", output_index=0, content_index=index, delta="text"
    )
    with pytest.raises(ProviderStreamError, match="invalid content index"):
        _ = [value async for value in ordered_output_events(stream([frame]))]


@pytest.mark.parametrize("delivery", ["terminal-only", "non-streaming", "item"])
async def test_null_completed_message_content_raises_structured_provider_error(
    delivery,
):
    from custom_components.extended_openai_conversation_responses.non_streaming import (
        completed_responses_events,
    )

    item = SimpleNamespace(type="message", role="assistant", content=None)
    response = SimpleNamespace(output=[item])
    if delivery == "non-streaming":
        frames = completed_responses_events(response)
    elif delivery == "item":
        frames = stream([event("response.output_item.done", output_index=0, item=item)])
    else:
        frames = stream([event("response.completed", response=response)])
    with pytest.raises(ProviderStreamError, match="message content must be an array"):
        _ = [value async for value in ordered_output_events(frames)]


@pytest.mark.parametrize("identified", [False, True])
@pytest.mark.parametrize("completion_has_id", [False, True])
async def test_late_unindexed_refusal_snapshot_cannot_repeat_a_finalized_message(
    identified,
    completion_has_id,
):
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    frames = []
    for item_id, prefix, full in [
        ("first", "No", "No thanks."),
        ("second", "Later", "Later today."),
    ]:
        item = SimpleNamespace(
            type="message",
            id=item_id,
            content=[SimpleNamespace(type="refusal", refusal=full)],
        )
        identity = {"item_id": item_id} if identified else {}
        completed = (
            item
            if completion_has_id
            else SimpleNamespace(type=item.type, content=item.content)
        )
        frames.extend(
            [
                event("response.output_item.added", item=item),
                event(
                    "response.refusal.delta", delta=prefix, content_index=0, **identity
                ),
                event("response.output_item.done", item=completed),
                event(
                    "response.refusal.done", refusal=full, content_index=0, **identity
                ),
            ]
        )
    frames.append(event("response.completed", response=SimpleNamespace(usage=None)))
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    parsed = entity._transform_responses_stream(
        SimpleNamespace(content=[]), stream(frames)
    )
    assert [delta["content"] async for delta in parsed if "content" in delta] == [
        "No",
        " thanks.",
        "Later",
        " today.",
    ]


async def test_consecutive_unindexed_messages_complete_their_own_text():
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    events = []
    for index, prefix, completion in [
        (0, "Long prefix", "Long prefix complete"),
        (1, "Next", "Next reply"),
    ]:
        item = SimpleNamespace(
            id=f"message_{index}",
            type="message",
            role="assistant",
            content=[SimpleNamespace(type="output_text", text=completion)],
        )
        events.extend(
            [
                event("response.output_item.added", item=item),
                event("response.output_text.delta", content_index=0, delta=prefix),
                event("response.output_text.done", content_index=0, text=completion),
                event("response.output_item.done", item=item),
            ]
        )
    events.append(event("response.completed", response=SimpleNamespace(usage=None)))
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    parsed = entity._transform_responses_stream(
        SimpleNamespace(content=[]), stream(events)
    )
    assert [delta["content"] async for delta in parsed if "content" in delta] == [
        "Long prefix",
        " complete",
        "Next",
        " reply",
    ]


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

@pytest.mark.parametrize("index", [-1, True, []], ids=["negative", "bool", "unhashable"])
async def test_invalid_index_is_rejected_before_budget_accounting(index):
    delta = event("response.output_text.delta", output_index=index, delta="Waiting")
    with pytest.raises(ProviderStreamError, match="invalid output index"):
        _ = [frame async for frame in ordered_output_events(stream([delta]))]


async def test_preannouncement_delta_and_item_must_share_identity():
    events = [
        event("response.output_text.delta", output_index=0, item_id="msg_other", delta="Unowned"),
        event("response.output_item.done", output_index=0),
        event("response.completed"),
    ]
    with pytest.raises(ProviderStreamError, match="conflicting output item id"):
        _ = [frame async for frame in ordered_output_events(stream(events))]


async def test_late_annotation_does_not_reopen_completed_item():
    frames = [
        event("response.output_item.added", output_index=0),
        event("response.output_item.done", output_index=0),
        event("response.output_text.annotation.added", output_index=0, item_id="msg_0", annotation={}),
        event("response.completed"),
    ]
    assert [frame async for frame in ordered_output_events(stream(frames))] == frames


async def test_unannounced_content_uses_the_backlog_budget(monkeypatch):
    from custom_components.extended_openai_conversation_responses import provider_output_budget

    monkeypatch.setattr(provider_output_budget, "MAX_BUFFERED_EVENTS", 1)
    delta = event("response.output_text.delta", output_index=0, item_id="msg_0", delta="Waiting")
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        _ = [frame async for frame in ordered_output_events(stream([delta, delta]))]
