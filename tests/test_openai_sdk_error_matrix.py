"""Provider failure taxonomy exercised through the installed OpenAI SDK wire."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    classify_config_provider_error,
    provider_user_message,
)
from tests.test_openai_sdk_wire import (
    _chat_log,
    _chat_text_stream,
    _chat_tool_stream,
    _client,
    _close_client,
    _entity,
    _responses_text_stream,
    _responses_tool_stream,
    _tool,
    _tool_result,
    _stream_response,
    _Wire,
)

API_MODES = (API_MODE_RESPONSES, API_MODE_CHAT_COMPLETIONS)
STATUS_CASES = (
    (400, "provider_error"),
    (401, "invalid_auth"),
    (403, "provider_forbidden"),
    (404, "provider_error"),
    (408, "provider_error"),
    (409, "provider_error"),
    (429, "provider_rate_limited"),
    (500, "provider_unavailable"),
    (502, "provider_unavailable"),
    (503, "provider_unavailable"),
)


def _text_reply(api_mode: str) -> Callable[[httpx.Request], httpx.Response]:
    return _stream_response(
        _responses_text_stream("Recovered")
        if api_mode == API_MODE_RESPONSES
        else _chat_text_stream("Recovered")
    )


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize(("status", "category"), STATUS_CASES)
async def test_real_sdk_http_error_is_classified_once_then_next_turn_succeeds(
    hass, api_mode: str, status: int, category: str
) -> None:
    """HTTP errors cannot create tool work or poison the next provider request."""

    def failure(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"content-type": "application/json", "x-request-id": "req-wire"},
            json={
                "error": {
                    "message": "model not found" if status == 404 else "wire failure",
                    "type": "invalid_request_error" if status < 500 else "server_error",
                    "code": "model_not_found" if status == 404 else "wire_error",
                }
            },
            request=request,
        )

    wire = _Wire([failure, _text_reply(api_mode)])
    client = _client(wire)
    entity = _entity(hass, client, api_mode)
    chat_log = _chat_log(hass)
    try:
        with pytest.raises(APIStatusError) as caught:
            await entity._async_handle_chat_log(chat_log, [], [])
        assert caught.value.status_code == status
        assert classify_config_provider_error(caught.value) == category
        message = provider_user_message(caught.value)
        assert f"HTTP {status}" in message
        assert "sk-wire-test" not in message
        assert len(wire.requests) == 1
        await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await _close_client(client)
    assert len(wire.requests) == 2
    assert chat_log.content[-1].content == "Recovered"


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize(
    ("transport_error", "sdk_error"),
    [
        (httpx.ConnectError("connection failed"), APIConnectionError),
        (httpx.ConnectTimeout("connect timeout"), APITimeoutError),
        (httpx.ReadTimeout("read timeout"), APITimeoutError),
        (httpx.RemoteProtocolError("closed before headers"), APIConnectionError),
        (httpx.ReadError("connection closed"), APIConnectionError),
    ],
)
async def test_real_sdk_transport_error_is_cleaned_up_and_next_turn_succeeds(
    hass,
    api_mode: str,
    transport_error: httpx.RequestError,
    sdk_error: type[Exception],
) -> None:
    """Connection failures and timeouts are delivered by the SDK without replay."""

    def failure(_request: httpx.Request) -> httpx.Response:
        raise transport_error

    wire = _Wire([failure, _text_reply(api_mode)])
    client = _client(wire)
    entity = _entity(hass, client, api_mode)
    chat_log = _chat_log(hass)
    try:
        with pytest.raises(sdk_error) as caught:
            await entity._async_handle_chat_log(chat_log, [], [])
        assert classify_config_provider_error(caught.value) == "cannot_connect"
        assert len(wire.requests) == 1
        await entity._async_handle_chat_log(chat_log, [], [])
    finally:
        await _close_client(client)
    assert len(wire.requests) == 2
    assert chat_log.content[-1].content == "Recovered"


class _InterruptedBody(httpx.AsyncByteStream):
    """Return headers and partial SSE before interrupting the real SDK reader."""

    def __init__(self, payload, error):
        self.payload = payload
        self.error = error
        self.closed = False

    async def __aiter__(self):
        yield self.payload
        raise self.error

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("api_mode", API_MODES)
@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout, asyncio.CancelledError])
@pytest.mark.parametrize("after_tool", [False, True])
async def test_real_sdk_open_body_interruption(hass, api_mode, error_type, after_tool):
    from unittest.mock import AsyncMock

    tool_bytes = _responses_tool_stream() if api_mode == API_MODE_RESPONSES else _chat_tool_stream()
    # Emit a partial function call, before the completion marker/arguments finish.
    partial = (tool_bytes.split(b"\n\n")[0] + b"\n\n").replace(b"call_response_1", b"call_partial").replace(b"call_chat_1", b"call_partial").replace(b"fc_response_1", b"fc_partial")
    raw = error_type("private transport detail")
    body = _InterruptedBody(partial, raw)

    def failure(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body, request=request)

    wire = _Wire(([_stream_response(tool_bytes)] if after_tool else []) + [failure])
    client = _client(wire)
    entity = _entity(hass, client, api_mode)
    entity._execute_function_tool = AsyncMock(side_effect=lambda _tool, call, *_: _tool_result(entity, call))
    log = _chat_log(hass)
    try:
        with pytest.raises(asyncio.CancelledError if error_type is asyncio.CancelledError else OpenAIError) as caught:
            await entity._async_handle_chat_log(log, [_tool()], [])
        if error_type is asyncio.CancelledError:
            assert caught.value is raw
        else:
            assert caught.value.__cause__ is raw
            assert "private transport detail" not in str(caught.value)
            assert classify_config_provider_error(caught.value) == "cannot_connect"
        assert len(wire.requests) == (2 if after_tool else 1)
        assert entity._execute_function_tool.await_count == int(after_tool)
        assert body.closed
        calls = [call for item in log.content for call in (getattr(item, "tool_calls", None) or [])]
        results = {getattr(item, "tool_call_id", None) for item in log.content}
        assert all(call.id in results for call in calls)
    finally:
        await _close_client(client)
