"""Provider output limits fail before executable calls or retained replay data."""

from types import SimpleNamespace as NS

import pytest

from custom_components.extended_openai_conversation_responses import (
    provider_output_budget as limits,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.non_streaming import (
    completed_chat_chunks,
    completed_responses_events,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from custom_components.extended_openai_conversation_responses.responses_stream_order import (
    ordered_output_events,
)
from tests.test_function_tool_error_recovery import _chat_log


def chunk(arguments=None, finish=None, content=None):
    calls = (
        [
            NS(
                index=0,
                id="call" if finish else None,
                function=NS(name="effect" if finish else None, arguments=arguments),
            )
        ]
        if arguments is not None
        else None
    )
    return NS(
        choices=[
            NS(
                delta=NS(content=content, refusal=None, tool_calls=calls),
                finish_reason=finish,
            )
        ],
        usage=None,
    )


async def source(values):
    for value in values:
        yield value


def events(arguments):
    item = NS(
        type="function_call",
        id="item",
        call_id="call",
        name="effect",
        arguments=arguments,
    )
    return [
        NS(
            type="response.output_item.added",
            output_index=0,
            item=NS(**{**vars(item), "arguments": ""}),
        ),
        NS(
            type="response.function_call_arguments.delta",
            output_index=0,
            item_id="item",
            delta=arguments,
        ),
        NS(type="response.output_item.done", output_index=0, item=item),
        NS(type="response.completed", response=NS(usage=None)),
    ]


async def parse(hass, mode, values):
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = NS(data={})
    log = _chat_log(hass)
    parser = (
        entity._transform_chat_stream
        if mode == "chat"
        else entity._transform_responses_stream
    )
    return [value async for value in parser(log, values)]


@pytest.mark.parametrize("mode", ["chat", "responses"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_oversized_arguments_never_become_executable(
    hass, monkeypatch, mode, streaming
):
    monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 64)
    arguments = '{"payload":"' + "x" * 100 + '"}'
    if streaming:
        values = source(
            [chunk(arguments, "tool_calls")] if mode == "chat" else events(arguments)
        )
    elif mode == "chat":
        values = completed_chat_chunks(
            NS(
                choices=[
                    NS(
                        message=NS(
                            content=None,
                            refusal=None,
                            tool_calls=[
                                NS(
                                    id="call",
                                    function=NS(name="effect", arguments=arguments),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=None,
            )
        )
    else:
        values = completed_responses_events(
            NS(output=[events(arguments)[2].item], status="completed", usage=None)
        )
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        await parse(hass, mode, values)


@pytest.mark.parametrize("fragments", [1, 10, 100])
@pytest.mark.parametrize("mode", ["chat", "responses"])
async def test_chunk_boundaries_preserve_logical_argument_admission(
    hass, monkeypatch, mode, fragments
):
    arguments = '{"payload":"' + "x" * 1000 + '"}'
    monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", len(arguments))
    parts = [arguments[i : i + fragments] for i in range(0, len(arguments), fragments)]
    if mode == "chat":
        values = [chunk(part) for part in parts] + [chunk("", "tool_calls")]
    else:
        template = events(arguments)
        values = (
            [template[0]]
            + [
                NS(
                    type="response.function_call_arguments.delta",
                    output_index=0,
                    item_id="item",
                    delta=part,
                )
                for part in parts
            ]
            + template[2:]
        )
    result = await parse(hass, mode, source(values))
    calls = [call for value in result for call in value.get("tool_calls", [])]
    assert len(calls) == 1
    assert calls[0].tool_args == {"payload": "x" * 1000}


@pytest.mark.parametrize(
    "ceiling",
    [
        "MAX_BUFFERED_CHARACTERS",
        "MAX_BUFFERED_EVENTS",
        "MAX_OUTPUT_ITEMS",
        "MAX_OUTPUT_EVENTS",
    ],
)
async def test_unfinished_leader_cannot_retain_unbounded_backlog(monkeypatch, ceiling):
    monkeypatch.setattr(
        limits, ceiling, 512 if ceiling == "MAX_BUFFERED_CHARACTERS" else 4
    )
    closed = []
    consumed = []

    async def input_events():
        try:
            yield NS(
                type="response.output_item.added",
                output_index=0,
                item=NS(type="message", id="first"),
            )
            for index in range(1, 20):
                consumed.append(index)
                yield NS(
                    type="response.output_item.added",
                    output_index=index,
                    item=NS(type="message", id=f"later{index}"),
                )
                yield NS(
                    type="response.output_text.delta",
                    output_index=index,
                    delta="x" * 100,
                )
        finally:
            closed.append(True)

    with pytest.raises(ProviderStreamError, match="local safety limits"):
        _ = [event async for event in ordered_output_events(input_events())]
    assert closed == [True]
    assert len(consumed) < 20


async def test_native_reasoning_is_bounded_before_replay_retention(hass, monkeypatch):
    monkeypatch.setattr(limits, "MAX_OUTPUT_CHARACTERS", 512)
    item = NS(type="reasoning", id="reasoning", encrypted_content="secret" * 1000)
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        await parse(
            hass,
            "responses",
            source([NS(type="response.output_item.done", output_index=0, item=item)]),
        )


def test_repeated_native_snapshots_and_refusal_completion_are_not_new_content():
    budget = limits.ProviderOutputBudget()
    item = NS(
        type="message", id="msg", content=[NS(type="refusal", refusal="x" * 1000)]
    )
    budget.responses_event(
        NS(type="response.output_item.added", output_index=0, item=item)
    )
    before = budget.total
    for delta in ["x" * 500, "x" * 500]:
        budget.responses_event(
            NS(
                type="response.refusal.delta",
                output_index=0,
                content_index=0,
                delta=delta,
            )
        )
    budget.responses_event(
        NS(
            type="response.refusal.done",
            output_index=0,
            content_index=0,
            refusal="x" * 1000,
        )
    )
    budget.responses_event(
        NS(type="response.output_item.done", output_index=0, item=item)
    )
    assert budget.total == before


async def test_argument_oracle_detects_removed_ceiling(hass, monkeypatch):
    original = limits.ProviderOutputBudget._account

    def bypass(self, key, **kwargs):
        with monkeypatch.context() as patch:
            patch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 10**9)
            return original(self, key, **kwargs)

    monkeypatch.setattr(limits.ProviderOutputBudget, "_account", bypass)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        await test_oversized_arguments_never_become_executable(
            hass, monkeypatch, "chat", True
        )


async def test_backlog_reservation_is_released_before_later_interleaving(monkeypatch):
    monkeypatch.setattr(limits, "MAX_BUFFERED_EVENTS", 2)

    def item(kind, index):
        return NS(
            type=kind, output_index=index, item=NS(type="message", id=f"msg{index}")
        )

    values = [
        item("response.output_item.added", 0),
        item("response.output_item.added", 1),
        NS(type="response.output_text.delta", output_index=1, delta="a"),
        item("response.output_item.done", 0),
        item("response.output_item.added", 2),
        NS(type="response.output_text.delta", output_index=2, delta="b"),
        item("response.output_item.done", 1),
        item("response.output_item.done", 2),
        NS(type="response.completed"),
    ]
    result = [event async for event in ordered_output_events(source(values))]
    assert [
        event.delta for event in result if event.type == "response.output_text.delta"
    ] == ["a", "b"]
    assert [
        event.output_index
        for event in result
        if event.type == "response.output_item.done"
    ] == [0, 1, 2]


async def test_backlog_oracle_detects_missing_admission(monkeypatch):
    monkeypatch.setattr(limits.ProviderOutputBudget, "buffer", lambda *_: 0)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        await test_unfinished_leader_cannot_retain_unbounded_backlog(
            monkeypatch, "MAX_BUFFERED_CHARACTERS"
        )


async def test_real_provider_loop_closes_oversized_source_without_dispatch(
    hass, monkeypatch
):
    from tests.test_conversation_provider_loop_contracts import _provider_fixture

    monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 64)
    closed = []

    class Stream:
        def __aiter__(self):
            return source(events('{"payload":"' + "secret" * 100 + '"}'))

        async def close(self):
            closed.append(True)

    agent, client, _, _, invoke = _provider_fixture(hass, monkeypatch, [Stream()])
    result = await invoke()
    assert result.response.error_code is not None
    agent._execute_function_tool.assert_not_awaited()
    assert client.responses.create.await_count == 1
    assert closed == [True]


async def test_native_empty_scalar_flood_is_not_zero_cost(hass, monkeypatch):
    monkeypatch.setattr(limits, "MAX_OUTPUT_CHARACTERS", 512)
    item = NS(type="reasoning", id="item", summary=[""] * 1000)
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        await parse(
            hass,
            "responses",
            source([NS(type="response.output_item.done", output_index=0, item=item)]),
        )


@pytest.mark.parametrize("identity", [None, "reused"])
async def test_unindexed_starts_cannot_bypass_item_ceiling(monkeypatch, identity):
    monkeypatch.setattr(limits, "MAX_OUTPUT_ITEMS", 4)
    values = [
        NS(type="response.output_item.added", item=NS(type="message", id=identity))
        for _ in range(5)
    ]
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        _ = [event async for event in ordered_output_events(source(values))]


@pytest.mark.parametrize("mode", ["chat", "responses"])
async def test_small_fragments_cannot_evade_aggregate_output_ceiling(
    hass, monkeypatch, mode
):
    monkeypatch.setattr(limits, "MAX_OUTPUT_CHARACTERS", 1024)
    if mode == "chat":
        values = [
            chunk(content="a" * 600),
            chunk(content="b" * 600),
            chunk(finish="stop"),
        ]
    else:
        item = NS(type="message", id="msg", content=[])
        values = [
            NS(type="response.output_item.added", output_index=0, item=item),
            NS(type="response.output_text.delta", output_index=0, delta="a" * 600),
            NS(type="response.output_text.delta", output_index=0, delta="b" * 600),
            NS(type="response.output_item.done", output_index=0, item=item),
            NS(type="response.completed", response=NS(usage=None)),
        ]
    with pytest.raises(ProviderStreamError, match="local safety limits"):
        await parse(hass, mode, source(values))


async def test_aggregate_oracle_detects_counter_reset_between_chunks(hass, monkeypatch):
    original = limits.ProviderOutputBudget.chat_delta

    def reset_per_chunk(self, delta):
        self.total = 0
        return original(self, delta)

    monkeypatch.setattr(limits.ProviderOutputBudget, "chat_delta", reset_per_chunk)
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        await test_small_fragments_cannot_evade_aggregate_output_ceiling(
            hass, monkeypatch, "chat"
        )
