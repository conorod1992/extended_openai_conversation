"""Wire-level tests using the real OpenAI SDK with an in-memory HTTP transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
from openai import AsyncOpenAI, RateLimitError
import pytest

from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)


MODEL = "gpt-4.1-mini"


def _sse(events: list[dict[str, Any]]) -> bytes:
    """Encode provider events as the raw SSE bytes consumed by the OpenAI SDK."""
    payload = "".join(
        f"data: {json.dumps(event, separators=(',', ':'))}\n\n" for event in events
    )
    return f"{payload}data: [DONE]\n\n".encode()


def _response_usage() -> dict[str, Any]:
    return {
        "input_tokens": 10,
        "input_tokens_details": {
            "cache_write_tokens": 0,
            "cached_tokens": 2,
        },
        "output_tokens": 4,
        "output_tokens_details": {"reasoning_tokens": 1},
        "total_tokens": 14,
    }


def _response_object(
    response_id: str,
    output: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the required core of an OpenAI Responses API response object."""
    return {
        "id": response_id,
        "created_at": 1.0,
        "model": MODEL,
        "object": "response",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": "completed",
        "usage": _response_usage(),
    }


def _responses_tool_stream() -> bytes:
    arguments = '{"entity_id":"light.kitchen"}'
    added_item = {
        "type": "function_call",
        "id": "fc_response_1",
        "call_id": "call_response_1",
        "name": "get_state",
        "arguments": "",
        "status": "in_progress",
    }
    done_item = {**added_item, "arguments": arguments, "status": "completed"}
    return _sse(
        [
            {
                "type": "response.output_item.added",
                "sequence_number": 0,
                "output_index": 0,
                "item": added_item,
            },
            {
                "type": "response.function_call_arguments.delta",
                "sequence_number": 1,
                "item_id": "fc_response_1",
                "output_index": 0,
                "delta": arguments,
            },
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": 2,
                "item_id": "fc_response_1",
                "output_index": 0,
                "name": "get_state",
                "arguments": arguments,
            },
            {
                "type": "response.output_item.done",
                "sequence_number": 3,
                "output_index": 0,
                "item": done_item,
            },
            {
                "type": "response.completed",
                "sequence_number": 4,
                "response": _response_object("resp_response_1", [done_item]),
            },
        ]
    )


def _responses_text_stream(text: str) -> bytes:
    added_item = {
        "type": "message",
        "id": "msg_response_2",
        "role": "assistant",
        "content": [],
        "status": "in_progress",
    }
    done_item = {
        **added_item,
        "content": [
            {
                "type": "output_text",
                "annotations": [],
                "logprobs": [],
                "text": text,
            }
        ],
        "status": "completed",
    }
    return _sse(
        [
            {
                "type": "response.output_item.added",
                "sequence_number": 0,
                "output_index": 0,
                "item": added_item,
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "item_id": "msg_response_2",
                "output_index": 0,
                "content_index": 0,
                "delta": text,
                "logprobs": [],
            },
            {
                "type": "response.output_item.done",
                "sequence_number": 2,
                "output_index": 0,
                "item": done_item,
            },
            {
                "type": "response.completed",
                "sequence_number": 3,
                "response": _response_object("resp_response_2", [done_item]),
            },
        ]
    )


def _chat_chunk(
    *,
    delta: dict[str, Any],
    finish_reason: str | None,
    chunk_id: str = "chatcmpl-wire",
) -> dict[str, Any]:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": 1,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
    }


def _chat_tool_stream() -> bytes:
    return _sse(
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
                                "arguments": '{"entity_id":',
                            },
                        }
                    ],
                },
                finish_reason=None,
            ),
            _chat_chunk(
                delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": '"light.kitchen"}'},
                        }
                    ]
                },
                finish_reason="tool_calls",
            ),
        ]
    )


def _chat_text_stream(text: str) -> bytes:
    return _sse(
        [
            _chat_chunk(
                delta={"role": "assistant", "content": text},
                finish_reason="stop",
                chunk_id="chatcmpl-wire-final",
            ),
            {
                "id": "chatcmpl-wire-final",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": MODEL,
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "total_tokens": 13,
                },
            },
        ]
    )


class _Wire:
    """Queue deterministic raw HTTP responses and retain every real SDK request."""

    def __init__(self, responders: list[Callable[[httpx.Request], httpx.Response]]) -> None:
        self._responders = list(responders)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responders:
            raise AssertionError(f"Unexpected OpenAI SDK request: {request.method} {request.url}")
        return self._responders.pop(0)(request)


def _stream_response(body: bytes) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
            request=request,
        )

    return respond


def _client(wire: _Wire) -> AsyncOpenAI:
    """Construct the real pinned SDK while preventing any network socket access."""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(wire))
    return AsyncOpenAI(
        api_key="sk-wire-test",
        base_url="https://openai-wire.invalid/v1",
        http_client=http_client,
        max_retries=0,
    )


async def _close_client(client: AsyncOpenAI) -> None:
    """Close the SDK client and let scheduled stream finalizers finish."""
    await client.close()
    await asyncio.sleep(0)


def _entity(hass: Any, client: AsyncOpenAI, api_mode: str) -> Any:
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client, data={})
    entity.subentry = SimpleNamespace(
        data={CONF_CHAT_MODEL: MODEL, CONF_API_MODE: api_mode}
    )
    entity.hass = hass
    entity.entity_id = "conversation.sdk_wire"
    entity._usage = None
    return entity


def _chat_log(hass: Any) -> conversation.ChatLog:
    chat_log = conversation.ChatLog(hass, "wire-conversation")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_user_content(conversation.UserContent(content="Check the light"))
    return chat_log


def _tool() -> dict[str, Any]:
    return {
        "spec": {
            "name": "get_state",
            "description": "Read one entity state",
            "parameters": {
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "native", "name": "unused-in-wire-test"},
    }


def _tool_result(
    entity: Any, tool_input: Any, result: str = "on"
) -> conversation.ToolResultContent:
    return conversation.ToolResultContent(
        agent_id=entity.entity_id,
        tool_call_id=tool_input.id,
        tool_name=tool_input.tool_name,
        tool_result={"result": result},
    )


def _json_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode())


async def test_responses_real_sdk_serializes_and_parses_tool_round_trip(hass) -> None:
    """Raw Responses SSE passes through the real SDK and returns on the next wire request."""
    wire = _Wire(
        [
            _stream_response(_responses_tool_stream()),
            _stream_response(_responses_text_stream("The kitchen light is on.")),
        ]
    )
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_RESPONSES)
    executed: list[tuple[str, str, dict[str, Any]]] = []

    async def execute(
        _function_tool: dict[str, Any],
        tool_input: Any,
        _llm_context: Any,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        executed.append((tool_input.id, tool_input.tool_name, tool_input.tool_args))
        return _tool_result(entity, tool_input)

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    chat_log = _chat_log(hass)

    try:
        await entity._async_handle_chat_log(chat_log, [_tool()], [])
    finally:
        await _close_client(client)

    assert executed == [
        ("call_response_1", "get_state", {"entity_id": "light.kitchen"})
    ]
    assert len(wire.requests) == 2
    assert [request.url.path for request in wire.requests] == [
        "/v1/responses",
        "/v1/responses",
    ]
    assert wire.requests[0].headers["authorization"] == "Bearer sk-wire-test"

    first = _json_body(wire.requests[0])
    assert first["stream"] is True
    assert first["store"] is False
    assert first["model"] == MODEL
    assert first["input"][:2] == [
        {"type": "message", "role": "system", "content": "Be helpful"},
        {"type": "message", "role": "user", "content": "Check the light"},
    ]
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["name"] == "get_state"

    second = _json_body(wire.requests[1])
    provider_call = next(
        item for item in second["input"] if item.get("type") == "function_call"
    )
    provider_output = next(
        item
        for item in second["input"]
        if item.get("type") == "function_call_output"
    )
    assert provider_call["call_id"] == "call_response_1"
    assert provider_call["name"] == "get_state"
    assert json.loads(provider_call["arguments"]) == {"entity_id": "light.kitchen"}
    assert provider_output["call_id"] == "call_response_1"
    assert json.loads(provider_output["output"]) == {"result": "on"}
    assert chat_log.content[-1].content == "The kitchen light is on."


async def test_chat_completions_real_sdk_serializes_and_parses_tool_round_trip(
    hass,
) -> None:
    """Raw Chat Completions SSE is parsed by the SDK before integration tool handling."""
    wire = _Wire(
        [
            _stream_response(_chat_tool_stream()),
            _stream_response(_chat_text_stream("The kitchen light is on.")),
        ]
    )
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_CHAT_COMPLETIONS)
    executed: list[tuple[str, str, dict[str, Any]]] = []

    async def execute(
        _function_tool: dict[str, Any],
        tool_input: Any,
        _llm_context: Any,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        executed.append((tool_input.id, tool_input.tool_name, tool_input.tool_args))
        return _tool_result(entity, tool_input)

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    chat_log = _chat_log(hass)

    try:
        await entity._async_handle_chat_log(chat_log, [_tool()], [])
    finally:
        await _close_client(client)

    assert executed == [("call_chat_1", "get_state", {"entity_id": "light.kitchen"})]
    assert len(wire.requests) == 2
    assert [request.url.path for request in wire.requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]

    first = _json_body(wire.requests[0])
    assert first["stream"] is True
    assert first["stream_options"] == {"include_usage": True}
    assert first["model"] == MODEL
    assert first["messages"][:2] == [
        {"role": "system", "content": "Be helpful"},
        {"role": "user", "content": "Check the light"},
    ]
    assert first["tools"][0]["type"] == "function"
    assert first["tools"][0]["function"]["name"] == "get_state"

    second = _json_body(wire.requests[1])
    assistant_with_call = next(
        message
        for message in second["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    tool_output = next(
        message for message in second["messages"] if message.get("role") == "tool"
    )
    provider_call = assistant_with_call["tool_calls"][0]
    assert provider_call["id"] == "call_chat_1"
    assert provider_call["function"]["name"] == "get_state"
    assert json.loads(provider_call["function"]["arguments"]) == {
        "entity_id": "light.kitchen"
    }
    assert tool_output["tool_call_id"] == "call_chat_1"
    assert json.loads(tool_output["content"]) == {"result": "on"}
    assert chat_log.content[-1].content == "The kitchen light is on."


async def test_real_sdk_maps_raw_http_429_without_network_or_retry(hass) -> None:
    """A raw provider 429 becomes the SDK's real RateLimitError exactly once."""

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"content-type": "application/json", "retry-after": "1"},
            json={
                "error": {
                    "message": "wire rate limit",
                    "type": "rate_limit_error",
                    "param": None,
                    "code": "rate_limit_exceeded",
                }
            },
            request=request,
        )

    wire = _Wire([rate_limited])
    client = _client(wire)
    entity = _entity(hass, client, API_MODE_CHAT_COMPLETIONS)
    chat_log = _chat_log(hass)

    try:
        with pytest.raises(RateLimitError, match="wire rate limit") as err:
            await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await _close_client(client)

    assert err.value.status_code == 429
    assert len(wire.requests) == 1
    assert wire.requests[0].url.path == "/v1/chat/completions"
