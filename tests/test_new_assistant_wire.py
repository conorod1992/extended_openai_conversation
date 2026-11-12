"""Fresh Luna defaults cross real SDK tool and structured-output wires."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses.ai_task import (
    ExtendedOpenAITaskEntity,
)
from custom_components.extended_openai_conversation_responses.config_flow import (
    DEFAULT_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_AI_TASK_OPTIONS,
)
from tests.test_openai_sdk_wire import (
    _chat_log,
    _client,
    _close_client,
    _entity,
    _json_body,
    _responses_text_stream,
    _responses_tool_stream,
    _stream_response,
    _tool,
    _tool_result,
    _Wire,
)


async def test_fresh_conversation_luna_tool_round_trip(hass):
    wire = _Wire(
        [
            _stream_response(_responses_tool_stream()),
            _stream_response(_responses_text_stream("The light is on")),
        ]
    )
    client = _client(wire)
    entity = _entity(hass, client, "auto")
    entity.subentry.data = dict(DEFAULT_OPTIONS)
    entity._execute_function_tool = AsyncMock(
        side_effect=lambda _tool, call, *_: _tool_result(entity, call)
    )
    try:
        await entity._async_handle_chat_log(_chat_log(hass), [_tool()], [])
        assert entity._execute_function_tool.await_count == 1
        assert len(wire.requests) == 2
        for request in wire.requests:
            body = _json_body(request)
            assert body["model"] == "gpt-6-luna"
            assert body["reasoning"] == {"effort": "low"}
            assert body["service_tier"] == "default"
            assert body["max_output_tokens"] == 500
            assert body["store"] is False
            assert request.url.path == "/v1/responses"
    finally:
        await _close_client(client)


@pytest.mark.parametrize("structured", [False, True])
async def test_fresh_ai_task_luna_plain_and_structured_output(hass, structured):
    wire = _Wire(
        [
            _stream_response(
                _responses_text_stream('{"answer":"ok"}' if structured else "ok")
            )
        ]
    )
    client = _client(wire)
    base = _entity(hass, client, "auto")
    base.subentry.data = dict(DEFAULT_AI_TASK_OPTIONS)
    base.entry.entry_id = "entry"
    base.subentry.subentry_id = "task"
    entity = ExtendedOpenAITaskEntity.__new__(ExtendedOpenAITaskEntity)
    entity.__dict__.update(base.__dict__)
    task = SimpleNamespace(
        name="answer",
        structure=vol.Schema({vol.Required("answer"): str}) if structured else None,
    )
    try:
        result = await entity._async_generate_data(task, _chat_log(hass))
        assert result.data == ({"answer": "ok"} if structured else "ok")
        body = _json_body(wire.requests[0])
        assert body["model"] == "gpt-6-luna"
        assert body["reasoning"] == {"effort": "low"}
        assert body["max_output_tokens"] == 500
        if structured:
            assert body["text"]["format"]["type"] == "json_schema"
            assert body["text"]["format"]["strict"] is True
    finally:
        await _close_client(client)
