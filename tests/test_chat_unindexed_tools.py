"""SDK-shaped omitted/null indices share admission and parser attribution."""

import asyncio

from openai.types.chat import ChatCompletionChunk
import pytest

from custom_components.extended_openai_conversation_responses import (
    provider_output_budget as limits,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    RequestTokenMeasurements,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from tests.test_output_error_usage_recovery import Stream
from tests.test_provider_tool_protocol import _chat_log, _entity, _FakeStream

_OMITTED = object()


def call(call_id=None, name=None, arguments=None, index=_OMITTED):
    value = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    if index is not _OMITTED:
        value["index"] = index
    return value


def chunk(*calls, terminal=None, usage=None):
    # The SDK constructs typed nested objects from wire data even when a
    # compatible endpoint omits a required field. Do not validate it away here.
    from openai._models import construct_type

    return construct_type(
        type_=ChatCompletionChunk,
        value={
            "id": "response",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": list(calls)},
                    "finish_reason": terminal,
                }
            ]
            if calls or terminal
            else [],
            "usage": usage,
        },
    )


async def parse(hass, chunks):
    output = [
        item
        async for item in _entity(hass, [])._transform_chat_stream(
            _chat_log(hass), _FakeStream(chunks)
        )
    ]
    return [
        (call.id, call.tool_name, call.tool_args)
        for item in output
        for call in item.get("tool_calls", [])
    ]


@pytest.mark.parametrize("index", [_OMITTED, None])
@pytest.mark.parametrize("separate", [False, True])
@pytest.mark.parametrize("same_function", [False, True])
async def test_multiple_envelopes_and_identified_argument_fragments(
    hass, index, separate, same_function
):
    first = call("call-a", "first", '{"value":', index)
    second = call("call-b", "first" if same_function else "second", '{"value":', index)
    chunks = [chunk(first), chunk(second)] if separate else [chunk(first, second)]
    chunks += [
        chunk(call("call-b", arguments="2}", index=index)),
        chunk(call("call-a", "first", "1}", index)),
        chunk(terminal="tool_calls"),
    ]
    assert await parse(hass, chunks) == [
        ("call-a", "first", {"value": 1}),
        ("call-b", "first" if same_function else "second", {"value": 2}),
    ]


@pytest.mark.parametrize("index", [_OMITTED, None])
async def test_single_unindexed_call_keeps_anonymous_fragment_compatibility(
    hass, index
):
    assert await parse(
        hass,
        [
            chunk(call("call-a", "first", '{"value":', index)),
            chunk(call(arguments="1}", index=index)),
            chunk(terminal="stop"),
        ],
    ) == [("call-a", "first", {"value": 1})]


async def test_mixed_index_namespaces_are_distinct_and_deterministic(hass):
    assert await parse(
        hass,
        [
            chunk(
                call("unindexed", "same", "{}"),
                call("high", "same", "{}", 7),
                call("low", "same", "{}", 0),
            ),
            chunk(terminal="tool_calls"),
        ],
    ) == [("low", "same", {}), ("high", "same", {}), ("unindexed", "same", {})]


@pytest.mark.parametrize("anonymous_first", [False, True])
async def test_ambiguous_fragment_fails_before_tool_publication_and_recovers_usage(
    hass, anonymous_first
):
    first = chunk(call("a", "same", '{"x":'))
    ambiguous = [call(arguments="1}"), call("b", "same", "{}")]
    if not anonymous_first:
        ambiguous.reverse()
    usage = RequestTokenMeasurements()
    source = Stream(
        [
            first,
            chunk(*ambiguous),
            chunk(
                usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
            ),
        ]
    )
    emitted = []
    with pytest.raises(ProviderStreamError, match="ambiguous"):
        async for item in _entity(hass, [])._transform_chat_stream(
            _chat_log(hass), source, usage
        ):
            emitted.append(item)
    assert not any("tool_calls" in item for item in emitted)
    assert usage.reported.total_tokens == 15


@pytest.mark.parametrize("index", [_OMITTED, None])
async def test_later_anonymous_fragment_is_not_assigned_to_newest_call(hass, index):
    with pytest.raises(ProviderStreamError, match="ambiguous"):
        await parse(
            hass,
            [
                chunk(call("a", "same", '{"x":', index)),
                chunk(call("b", "same", '{"x":', index)),
                chunk(call(arguments="1}", index=index)),
                chunk(terminal="tool_calls"),
            ],
        )


async def test_changed_name_on_stable_unindexed_id_is_ambiguous(hass):
    with pytest.raises(ProviderStreamError, match="ambiguous"):
        await parse(
            hass,
            [
                chunk(call("a", "first", "{}")),
                chunk(call("a", "second", "")),
                chunk(terminal="tool_calls"),
            ],
        )


@pytest.mark.parametrize("limit", ["items", "arguments", "output"])
async def test_normalized_calls_keep_all_admission_ceilings(hass, monkeypatch, limit):
    if limit == "items":
        monkeypatch.setattr(limits, "MAX_OUTPUT_ITEMS", 1)
        chunks = [chunk(call("a", "f", "{}"), call("b", "f", "{}"))]
    elif limit == "arguments":
        monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 4)
        chunks = [chunk(call("a", "f", '{"x"')), chunk(call("a", arguments=":1}"))]
    else:
        monkeypatch.setattr(limits, "MAX_OUTPUT_CHARACTERS", 6)
        chunks = [chunk(call("a", "f", "{}"), call("b", "f", "{}"))]
    with pytest.raises(ProviderStreamError, match="safety limits"):
        await parse(hass, [*chunks, chunk(terminal="tool_calls")])


def test_distinct_unindexed_calls_have_independent_argument_budgets(monkeypatch):
    monkeypatch.setattr(limits, "MAX_ARGUMENT_CHARACTERS", 4)
    budget = limits.ProviderOutputBudget()
    delta = chunk(call("a", "f", "{}"), call("b", "f", "{}")).choices[0].delta
    admitted = budget.chat_delta(delta)
    assert len(budget.items) == len({item.key for item in admitted}) == 2


def test_repeated_envelopes_cannot_create_unbounded_temporary_normalization(
    monkeypatch,
):
    monkeypatch.setattr(limits, "MAX_OUTPUT_NODES", 1)
    delta = chunk(call("a", "f", ""), call("a", "f", "")).choices[0].delta
    with pytest.raises(ProviderStreamError, match="safety limits"):
        limits.ProviderOutputBudget().chat_delta(delta)


async def test_cancellation_still_propagates_with_pending_unindexed_call(hass):
    class Cancelled:
        async def __aiter__(self):
            yield chunk(call("a", "first", '{"x":'))
            raise asyncio.CancelledError

    emitted = []
    with pytest.raises(asyncio.CancelledError):
        async for item in _entity(hass, [])._transform_chat_stream(
            _chat_log(hass), Cancelled()
        ):
            emitted.append(item)
    assert not any("tool_calls" in item for item in emitted)
