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


@pytest.mark.parametrize("phase", ["snapshot", "delta"])
async def test_mixed_index_tool_arguments_are_only_merged_after_both_snapshots(phase):
    item = SimpleNamespace(
        type="function_call", id="item-1", call_id="call-1", name="perform", arguments="{}"
    )
    kind = "response.function_call_arguments"
    # The unindexed snapshot arrives before the provider announces ownership.
    legacy = event(f"{kind}.done", item_id=item.id, arguments="{}")
    indexed = (
        event(f"{kind}.done", output_index=0, arguments="{}")
        if phase == "snapshot"
        else event(f"{kind}.delta", output_index=0, delta="{")
    )
    added = event("response.output_item.added", output_index=0, item=item)
    done = event("response.output_item.done", output_index=0, item=item)
    frames = ordered_output_events(stream([legacy, indexed, added, done]))
    if phase == "delta":
        with pytest.raises(ProviderStreamError, match="ambiguous mixed-index tool arguments"):
            _ = [frame async for frame in frames]
    else:
        presented = [frame async for frame in frames]
        assert presented.count(added) == presented.count(done) == 1
        assert presented[-1] is done


async def test_unindexed_snapshot_cannot_repeat_refusal_from_a_finished_indexed_item():
    item = SimpleNamespace(
        type="message", id="message-1", role="assistant",
        content=[SimpleNamespace(type="refusal", refusal="No.")],
    )
    added = event("response.output_item.added", item=item, output_index=0)
    done = event("response.output_item.done", item=item, output_index=0)
    late = event("response.refusal.done", item_id=item.id, refusal="No.", content_index=0)
    terminal = event("response.completed", response=SimpleNamespace(output=[item]))
    assert [
        frame async for frame in ordered_output_events(stream([added, done, late, terminal]))
    ] == [added, done, terminal]


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
@pytest.mark.parametrize(
    "snapshot_kind", ["response.refusal.done", "response.content_part.done"]
)
async def test_late_unindexed_refusal_snapshot_cannot_repeat_a_finalized_message(
    identified,
    completion_has_id,
    snapshot_kind,
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
                    snapshot_kind,
                    content_index=0,
                    **identity,
                    **(
                        {"part": item.content[0]}
                        if snapshot_kind == "response.content_part.done"
                        else {"refusal": full}
                    ),
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


@pytest.mark.parametrize(
    "case,reason",
    [
        ("duplicate-start", "repeated output item start"),
        ("late-delta", "content after completed output item"),
        ("changed-signature", "conflicting output item"),
        ("message-arguments", "non-function output item"),
        ("late-argument-delta", "tool argument delta after completion"),
        ("orphan-arguments", "no completed output item"),
        ("terminal-conflict", "terminal output conflicts"),
        ("legacy-duplicate", "repeated completed output item"),
    ],
)
async def test_conflicting_output_lifecycle_is_rejected(case, reason):
    message = SimpleNamespace(
        type="message", id="msg", role="assistant", content=[]
    )
    tool = SimpleNamespace(
        type="function_call", id="tool", call_id="call", name="test", arguments="{}"
    )
    added = "response.output_item.added"
    done = "response.output_item.done"
    arguments_done = "response.function_call_arguments.done"
    arguments_delta = "response.function_call_arguments.delta"
    if case == "duplicate-start":
        frames = [event(added, output_index=0, item=message)] * 2
    elif case == "late-delta":
        frames = [
            event(done, output_index=0, item=message),
            event("response.output_text.delta", output_index=0, delta="late"),
        ]
    elif case == "changed-signature":
        frames = [
            event(added, output_index=0, item=tool),
            event(
                done,
                output_index=0,
                item=SimpleNamespace(**{**vars(tool), "name": "other"}),
            ),
        ]
    elif case == "message-arguments":
        frames = [
            event(added, output_index=0, item=message),
            event(arguments_done, output_index=0, arguments="{}"),
        ]
    elif case == "late-argument-delta":
        frames = [
            event(added, output_index=0, item=tool),
            event(arguments_done, output_index=0, arguments="{}"),
            event(arguments_delta, output_index=0, delta=" "),
        ]
    elif case == "orphan-arguments":
        frames = [
            event(arguments_done, item_id="orphan", arguments="{}"),
            event("response.completed"),
        ]
    elif case == "terminal-conflict":
        frames = [
            event(done, output_index=0, item=tool),
            event("response.completed", response=SimpleNamespace(output=[message])),
        ]
    else:
        frames = [event(done, item=tool)] * 2
    with pytest.raises(ProviderStreamError, match=reason):
        _ = [frame async for frame in ordered_output_events(stream(frames))]


async def test_terminal_only_output_replays_text_but_never_local_tools():
    message = SimpleNamespace(
        type="message",
        id="message",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text="Recovered answer")],
    )
    terminal = event("response.completed", response=SimpleNamespace(output=[message]))
    parsed = [frame async for frame in ordered_output_events(stream([terminal]))]
    assert [frame.type for frame in parsed] == [
        "response.output_item.added",
        "response.output_text.delta",
        "response.output_item.done",
        "response.completed",
    ]
    assert parsed[1].delta == "Recovered answer"
    assert parsed[-1] is terminal

    for output, reason in [
        ([message, message], "duplicate terminal output item id"),
        (
            [
                message,
                SimpleNamespace(
                    type="function_call",
                    id="tool",
                    name="test",
                    call_id="call",
                    arguments="{}",
                ),
            ],
            "ambiguous terminal-only output",
        ),
    ]:
        bad = event("response.completed", response=SimpleNamespace(output=output))
        with pytest.raises(ProviderStreamError, match=reason):
            _ = [frame async for frame in ordered_output_events(stream([bad]))]


async def test_mixed_index_arguments_share_one_accumulation_limit(monkeypatch):
    from custom_components.extended_openai_conversation_responses import (
        provider_output_budget,
        responses_stream_order,
    )

    monkeypatch.setattr(provider_output_budget, "MAX_ARGUMENT_CHARACTERS", 4)
    monkeypatch.setattr(responses_stream_order, "MAX_ARGUMENT_CHARACTERS", 4)
    item = SimpleNamespace(
        type="function_call", id="tool", name="test", call_id="call", arguments=""
    )
    frames = [
        event("response.output_item.added", output_index=0, item=item),
        event("response.function_call_arguments.delta", output_index=0, delta="abc"),
        event("response.function_call_arguments.delta", item_id="tool", delta="def"),
    ]
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        _ = [frame async for frame in ordered_output_events(stream(frames))]


async def test_late_legacy_argument_snapshot_without_id_uses_completed_call():
    item = SimpleNamespace(
        type="function_call", id="tool", name="test", call_id="call", arguments="{}"
    )
    frames = [
        event("response.output_item.done", item=item),
        event("response.function_call_arguments.done", arguments="{}"),
        event("response.completed", response=SimpleNamespace(output=[item])),
    ]
    assert [frame async for frame in ordered_output_events(stream(frames))] == frames
    frames[1].arguments = '{"other":1}'
    with pytest.raises(ProviderStreamError, match="conflicting tool arguments"):
        _ = [frame async for frame in ordered_output_events(stream(frames))]


@pytest.mark.parametrize("owner", ["index", "id", "anonymous"])
@pytest.mark.parametrize("part_kind", ["output_text", "refusal", "content_part"])
@pytest.mark.parametrize("buffered", [False, True])
async def test_late_indexed_part_snapshot_is_not_presented_again(
    owner, part_kind, buffered
):
    index = int(buffered)
    field = "refusal" if part_kind == "refusal" else "text"
    part = SimpleNamespace(
        type="refusal" if field == "refusal" else "output_text",
        **{field: "Already complete"},
    )
    item = SimpleNamespace(type="message", id="message", role="assistant", content=[part])
    identity = {"index": {"output_index": index}, "id": {"item_id": item.id}}.get(
        owner, {}
    )
    opener = event("response.output_item.added", output_index=index, item=item)
    done = event("response.output_item.done", output_index=index, item=item)
    snapshot = event(
        f"response.{part_kind}.done",
        content_index=0,
        **identity,
        **({"part": part} if part_kind == "content_part" else {field: "Already complete"}),
    )
    output = [item]
    wire = [opener, done]
    presentation = wire.copy()
    if buffered:
        earlier = SimpleNamespace(type="reasoning", id="reasoning")
        earlier_open = event("response.output_item.added", output_index=0, item=earlier)
        earlier_done = event("response.output_item.done", output_index=0, item=earlier)
        wire.extend([earlier_open, earlier_done])
        presentation[:0] = [earlier_open, earlier_done]
        output.insert(0, earlier)
    terminal = event("response.completed", response=SimpleNamespace(output=output))
    assert [
        frame async for frame in ordered_output_events(stream([*wire, snapshot, terminal]))
    ] == [*presentation, terminal]


async def test_terminal_output_count_is_bounded_before_replay(monkeypatch):
    from custom_components.extended_openai_conversation_responses import (
        responses_stream_order,
    )

    monkeypatch.setattr(responses_stream_order, "MAX_OUTPUT_ITEMS", 1)
    items = [
        SimpleNamespace(type="message", id=str(index), role="assistant", content=[])
        for index in range(2)
    ]
    frame = event("response.completed", response=SimpleNamespace(output=items))
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        _ = [value async for value in ordered_output_events(stream([frame]))]


@pytest.mark.parametrize("terminal_has_snapshot", [False, True])
@pytest.mark.parametrize("introduction", ["opener", "item-id", "part-index"])
async def test_legacy_introduced_message_requires_completion(
    terminal_has_snapshot, introduction
):
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    opening = SimpleNamespace(type="message", id="message")
    complete = SimpleNamespace(
        type="message",
        id="message",
        status="completed",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text="Light is on")],
    )
    frames = []
    if introduction == "opener":
        frames.append(event("response.output_item.added", item=opening))
    metadata = {
        "opener": {},
        "item-id": {"item_id": "message"},
        "part-index": {"content_index": 0},
    }[introduction]
    frames.extend(
        [
            event("response.output_text.delta", delta="Light is ", **metadata),
            event(
                "response.completed",
                response=SimpleNamespace(
                    usage=None,
                    output=[complete] if terminal_has_snapshot else [],
                ),
            ),
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    seen = []
    parser = entity._transform_responses_stream(
        SimpleNamespace(content=[]), stream(frames)
    )
    if terminal_has_snapshot:
        seen = [delta.get("content", "") async for delta in parser]
        assert "".join(seen) == "Light is on"
    else:
        with pytest.raises(ProviderStreamError, match="unfinished legacy output item"):
            async for delta in parser:
                seen.append(delta.get("content", ""))
        assert not any(seen)


@pytest.mark.parametrize(
    "part_frame",
    [
        event("response.output_text.delta", content_index=0, delta="Light is "),
        event(
            "response.content_part.added",
            content_index=0,
            part=SimpleNamespace(type="output_text", text=""),
        ),
        event(
            "response.content_part.done",
            content_index=0,
            part=SimpleNamespace(type="output_text", text="Light is on"),
        ),
    ],
)
async def test_anonymous_numbered_part_can_complete_its_open_indexed_message(part_frame):
    previous = SimpleNamespace(
        type="message", id="previous", role="assistant", content=[]
    )
    item = SimpleNamespace(
        type="message",
        id="message",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text="Light is on")],
    )
    frames = [
        event("response.output_item.added", output_index=0, item=previous),
        event("response.output_item.done", output_index=0, item=previous),
        event("response.output_item.added", output_index=1, item=item),
        part_frame,
        event("response.output_item.done", output_index=1, item=item),
        event("response.completed", response=SimpleNamespace(output=[previous, item])),
    ]
    assert [frame async for frame in ordered_output_events(stream(frames))] == frames


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("part_kind", ["output_text", "refusal"])
async def test_anonymous_fragments_share_one_item_and_reconcile_terminal(indexed, part_kind):
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )
    from custom_components.extended_openai_conversation_responses.provider_output_budget import (
        MAX_OUTPUT_ITEMS,
    )

    field = "text" if part_kind == "output_text" else "refusal"
    count = MAX_OUTPUT_ITEMS + 1
    item = SimpleNamespace(
        type="message", role="assistant", id="answer", status="completed",
        content=[SimpleNamespace(type=part_kind, **{field: "x" * count + "!"})],
    )
    frames = []
    if indexed:
        frames.append(event("response.output_item.added", output_index=0, item=item))
    for _ in range(count):
        frames.append(event(f"response.{part_kind}.delta", delta="x"))
    if indexed:
        frames.append(event("response.output_item.done", output_index=0, item=item))
    frames.append(event("response.completed", response=SimpleNamespace(output=[item], usage=None)))
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    chunks = [
        delta["content"]
        async for delta in entity._transform_responses_stream(SimpleNamespace(content=[]), stream(frames))
        if "content" in delta
    ]
    assert "".join(chunks) == "x" * count + "!"


@pytest.mark.parametrize("case", ["conflict", "multiple", "non-assistant"])
async def test_implicit_anonymous_terminal_rejects_inconsistent_messages(case):
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    item = SimpleNamespace(
        type="message", role="user" if case == "non-assistant" else "assistant", id="answer",
        content=[SimpleNamespace(type="output_text", text="off" if case == "conflict" else "online")],
    )
    output = [item, item] if case == "multiple" else [item]
    frames = [
        event("response.output_text.delta", delta="on"),
        event("response.completed", response=SimpleNamespace(output=output, usage=None)),
    ]
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    with pytest.raises(ProviderStreamError):
        _ = [delta async for delta in entity._transform_responses_stream(SimpleNamespace(content=[]), stream(frames))]


@pytest.mark.parametrize("completion", ["part", "item"])
@pytest.mark.parametrize("part_kind", ["output_text", "refusal"])
async def test_conflicting_text_snapshot_cannot_supply_a_suffix(completion, part_kind):
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    field = "text" if part_kind == "output_text" else "refusal"
    snapshot = {field: "Light is off"}
    item = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type=part_kind, **snapshot)],
    )
    frames = [
        event("response.output_item.added", output_index=0, item=item),
        event(
            f"response.{part_kind}.delta",
            output_index=0,
            content_index=0,
            delta="Light is on",
        ),
    ]
    if completion == "part":
        frames.append(
            event(
                f"response.{part_kind}.done",
                output_index=0,
                content_index=0,
                **snapshot,
            )
        )
    frames.extend(
        [
            event("response.output_item.done", output_index=0, item=item),
            event("response.completed", response=SimpleNamespace(usage=None)),
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    seen = []
    with pytest.raises(ProviderStreamError, match="completed message text conflicts"):
        async for delta in entity._transform_responses_stream(
            SimpleNamespace(content=[]), stream(frames)
        ):
            seen.append(delta.get("content", ""))
    assert not any(seen)


async def test_later_part_waits_for_completed_earlier_text():
    from custom_components.extended_openai_conversation_responses.entity import (
        ExtendedOpenAIBaseLLMEntity,
    )

    item = SimpleNamespace(
        type="message",
        phase="final_answer",
        content=[
            SimpleNamespace(type="output_text", text="Status: "),
            SimpleNamespace(type="output_text", text="Off."),
        ],
    )
    frames = [
        event("response.output_item.added", output_index=0, item=item),
        event(
            "response.output_text.delta", output_index=0, content_index=0, delta="St"
        ),
        event(
            "response.output_text.delta", output_index=0, content_index=1, delta="Off"
        ),
        event(
            "response.output_text.done", output_index=0, content_index=1, text="Off."
        ),
        event("response.output_item.done", output_index=0, item=item),
        event("response.completed", response=SimpleNamespace(usage=None)),
    ]
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    deltas = [
        delta.get("content", "")
        async for delta in entity._transform_responses_stream(
            SimpleNamespace(content=[]),
            stream(frames),
        )
    ]
    assert "".join(deltas) == "Status: Off."


async def test_terminal_snapshot_conflicting_with_completed_message_is_rejected():
    completed = SimpleNamespace(
        id="message",
        type="message",
        role="assistant",
        content=[SimpleNamespace(type="output_text", text="Light is on")],
    )
    conflicting = SimpleNamespace(
        **{**vars(completed), "content": [SimpleNamespace(type="output_text", text="Light is off")]},
    )
    frames = [
        event("response.output_item.done", output_index=0, item=completed),
        event("response.completed", response=SimpleNamespace(output=[conflicting])),
    ]
    with pytest.raises(ProviderStreamError, match="terminal output conflicts"):
        _ = [frame async for frame in ordered_output_events(stream(frames))]
