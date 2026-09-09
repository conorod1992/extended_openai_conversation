"""Malformed and partial provider responses through the real OpenAI SDK wire path."""

from __future__ import annotations

import asyncio
import gc
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    ParseArgumentsFailed,
    TokenLengthExceededError,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from tests.test_openai_sdk_wire import (
    _Wire,
    _chat_chunk,
    _chat_log,
    _client,
    _entity,
    _response_object,
    _responses_tool_stream,
    _sse,
    _stream_response,
    _tool,
    _tool_result,
)


def _assert_no_tool_protocol_state(chat_log: conversation.ChatLog) -> None:
    """A rejected provider call must not leave a synthetic tool exchange behind."""
    assert not any(
        isinstance(item, conversation.AssistantContent) and item.tool_calls
        for item in chat_log.content
    )
    assert not any(
        isinstance(item, conversation.ToolResultContent) for item in chat_log.content
    )


def _assert_closed_tool_exchange(
    chat_log: conversation.ChatLog,
    *,
    call_id: str,
    tool_name: str,
) -> None:
    """A completed local side effect must retain exactly one matched call/result pair."""
    calls = [
        tool_call
        for item in chat_log.content
        if isinstance(item, conversation.AssistantContent) and item.tool_calls
        for tool_call in item.tool_calls
    ]
    outputs = [
        item
        for item in chat_log.content
        if isinstance(item, conversation.ToolResultContent)
    ]
    assert [(call.id, call.tool_name) for call in calls] == [(call_id, tool_name)]
    assert [(item.tool_call_id, item.tool_name) for item in outputs] == [
        (call_id, tool_name)
    ]


async def _drain_sdk_asyncgen_finalizers() -> None:
    """Finish transient async-generator cleanup scheduled by OpenAI's SSE iterator."""
    gc.collect()
    # OpenAI 2.45.0 breaks out of a nested async generator on [DONE]. Python
    # schedules that generator's athrow finalizer on the next loop turn; a second
    # turn lets the finalizer itself complete before HA checks for lingering tasks.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _responses_malformed_tool_arguments() -> bytes:
    item = {
        "type": "function_call",
        "id": "fc_bad_args",
        "call_id": "call_bad_args",
        "name": "get_state",
        "arguments": '{"entity_id":',
        "status": "completed",
    }
    return _sse(
        [
            {
                "type": "response.output_item.added",
                "sequence_number": 0,
                "output_index": 0,
                "item": {**item, "arguments": "", "status": "in_progress"},
            },
            {
                "type": "response.output_item.done",
                "sequence_number": 1,
                "output_index": 0,
                "item": item,
            },
        ]
    )


def _chat_malformed_tool_arguments() -> bytes:
    return _sse(
        [
            _chat_chunk(
                delta={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_bad_args",
                            "type": "function",
                            "function": {
                                "name": "get_state",
                                "arguments": '{"entity_id":',
                            },
                        }
                    ],
                },
                finish_reason="tool_calls",
            )
        ]
    )


def _responses_partial_text_stream() -> bytes:
    return _sse(
        [
            {
                "type": "response.output_item.added",
                "sequence_number": 0,
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": "msg_partial",
                    "role": "assistant",
                    "content": [],
                    "status": "in_progress",
                },
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "item_id": "msg_partial",
                "output_index": 0,
                "content_index": 0,
                "delta": "Partial answer",
                "logprobs": [],
            },
        ]
    )


def _chat_partial_text_stream() -> bytes:
    return _sse(
        [
            _chat_chunk(
                delta={"role": "assistant", "content": "Partial answer"},
                finish_reason=None,
                chunk_id="chatcmpl-partial",
            )
        ]
    )


def _responses_incomplete_stream(reason: str) -> bytes:
    response = _response_object("resp_incomplete", [])
    response["status"] = "incomplete"
    response["incomplete_details"] = {"reason": reason}
    return _sse(
        [
            {
                "type": "response.incomplete",
                "sequence_number": 0,
                "response": response,
            }
        ]
    )


def _responses_failed_stream() -> bytes:
    response = _response_object("resp_failed", [])
    response["status"] = "failed"
    response["error"] = {
        "code": "server_error",
        "message": "provider generation failed",
    }
    return _sse(
        [
            {
                "type": "response.failed",
                "sequence_number": 0,
                "response": response,
            }
        ]
    )


@pytest.mark.parametrize(
    ("api_mode", "body", "message"),
    [
        (
            API_MODE_RESPONSES,
            _responses_partial_text_stream(),
            "OpenAI Responses stream ended before a terminal event",
        ),
        (
            API_MODE_CHAT_COMPLETIONS,
            _chat_partial_text_stream(),
            "OpenAI Chat Completions stream ended before a terminal finish reason",
        ),
    ],
)
async def test_real_sdk_partial_stream_without_terminal_event_fails_closed(
    hass,
    api_mode: str,
    body: bytes,
    message: str,
) -> None:
    """A syntactically valid stream that ends early is never accepted as complete."""
    wire = _Wire([_stream_response(body)])
    client = _client(wire)
    entity = _entity(hass, client, api_mode)
    entity._execute_function_tool = AsyncMock()
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(HomeAssistantError, match=message):
            await entity._async_handle_chat_log(chat_log, [_tool()], [])
    finally:
        await client.close()
        await _drain_sdk_asyncgen_finalizers()

    entity._execute_function_tool.assert_not_awaited()
    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


@pytest.mark.parametrize(
    ("api_mode", "body"),
    [
        (API_MODE_RESPONSES, _responses_malformed_tool_arguments()),
        (API_MODE_CHAT_COMPLETIONS, _chat_malformed_tool_arguments()),
    ],
)
async def test_real_sdk_malformed_tool_arguments_never_execute_or_orphan_call(
    hass,
    api_mode: str,
    body: bytes,
) -> None:
    """Malformed JSON arguments parsed from the real SDK fail before side effects."""
    wire = _Wire([_stream_response(body)])
    client = _client(wire)
    entity = _entity(hass, client, api_mode)
    entity._execute_function_tool = AsyncMock()
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(ParseArgumentsFailed):
            await entity._async_handle_chat_log(chat_log, [_tool()], [])
    finally:
        await client.close()

    entity._execute_function_tool.assert_not_awaited()
    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


async def test_real_sdk_responses_incomplete_max_tokens_maps_to_token_limit(hass) -> None:
    """The SDK-parsed Responses incomplete event preserves the max-token contract."""
    wire = _Wire([_stream_response(_responses_incomplete_stream("max_output_tokens"))])
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_RESPONSES)
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(TokenLengthExceededError):
            await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await client.close()

    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


async def test_real_sdk_responses_incomplete_other_reason_fails_explicitly(hass) -> None:
    """Non-token incomplete Responses are surfaced rather than treated as success."""
    wire = _Wire([_stream_response(_responses_incomplete_stream("content_filter"))])
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_RESPONSES)
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(
            HomeAssistantError,
            match="OpenAI response incomplete: content_filter",
        ):
            await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await client.close()

    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


async def test_real_sdk_responses_failed_event_preserves_structured_failure(hass) -> None:
    """A provider failure emitted inside an HTTP 200 stream remains structured."""
    wire = _Wire([_stream_response(_responses_failed_stream())])
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_RESPONSES)
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(ProviderStreamError) as raised:
            await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await client.close()

    error = raised.value
    assert str(error) == "OpenAI response failed: provider generation failed"
    assert error.code == "server_error"
    assert error.response_id == "resp_failed"
    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


async def test_real_sdk_responses_error_event_preserves_code(hass) -> None:
    """A top-level Responses error event is parsed by the SDK then normalized safely."""
    body = _sse(
        [
            {
                "type": "error",
                "sequence_number": 0,
                "code": "stream_error",
                "message": "provider stream aborted",
                "param": None,
            }
        ]
    )
    wire = _Wire([_stream_response(body)])
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_RESPONSES)
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(ProviderStreamError) as raised:
            await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await client.close()

    error = raised.value
    assert str(error) == "OpenAI response error: provider stream aborted"
    assert error.code == "stream_error"
    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


async def test_real_sdk_chat_length_finish_maps_to_token_limit(hass) -> None:
    """Chat Completions finish_reason=length is rejected through the real SDK parser."""
    body = _sse(
        [
            _chat_chunk(
                delta={"role": "assistant", "content": "Cut off"},
                finish_reason="length",
                chunk_id="chatcmpl-length",
            )
        ]
    )
    wire = _Wire([_stream_response(body)])
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_CHAT_COMPLETIONS)
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(TokenLengthExceededError):
            await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await client.close()

    assert len(wire.requests) == 1
    _assert_no_tool_protocol_state(chat_log)


@pytest.mark.parametrize(
    ("api_mode", "first_body", "second_body", "call_id"),
    [
        (
            API_MODE_RESPONSES,
            _responses_tool_stream(),
            _responses_partial_text_stream(),
            "call_response_1",
        ),
        (
            API_MODE_CHAT_COMPLETIONS,
            _sse(
                [
                    _chat_chunk(
                        delta={
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_chat_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_state",
                                        "arguments": '{"entity_id":"light.kitchen"}',
                                    },
                                }
                            ],
                        },
                        finish_reason="tool_calls",
                    )
                ]
            ),
            _chat_partial_text_stream(),
            "call_chat_1",
        ),
    ],
)
async def test_real_sdk_failure_after_tool_keeps_one_closed_exchange_without_retry(
    hass,
    api_mode: str,
    first_body: bytes,
    second_body: bytes,
    call_id: str,
) -> None:
    """A failed follow-up never repeats a completed side effect or leaves an orphan."""
    wire = _Wire(
        [
            _stream_response(first_body),
            _stream_response(second_body),
        ]
    )
    client = _client(wire)
    entity = _entity(hass, client, api_mode)
    executed: list[str] = []

    async def execute(
        _function_tool: dict[str, Any],
        tool_input: Any,
        _llm_context: Any,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        executed.append(tool_input.id)
        return _tool_result(entity, tool_input)

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    chat_log = _chat_log(hass)

    expected_message = (
        "OpenAI Responses stream ended before a terminal event"
        if api_mode == API_MODE_RESPONSES
        else "OpenAI Chat Completions stream ended before a terminal finish reason"
    )
    try:
        with pytest.raises(HomeAssistantError, match=expected_message):
            await entity._async_handle_chat_log(chat_log, [_tool()], [])
    finally:
        await client.close()

    assert executed == [call_id]
    assert len(wire.requests) == 2
    _assert_closed_tool_exchange(
        chat_log,
        call_id=call_id,
        tool_name="get_state",
    )
