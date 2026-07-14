"""Tests for Chat Completions and Responses API adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation.const import (
    API_MODE_AUTO,
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
)
from custom_components.extended_openai_conversation.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_responses_param,
    _format_tools,
)
from custom_components.extended_openai_conversation.helpers import get_api_mode
from homeassistant.components import conversation


class FakeStream:
    """Async iterator over fake SDK stream events."""

    def __init__(self, events: list[Any]) -> None:
        self.events = events

    async def __aiter__(self) -> AsyncIterator[Any]:
        for event in self.events:
            yield event


class FakeReasoningItem:
    """Minimal serializable reasoning response item."""

    type = "reasoning"

    def __init__(self) -> None:
        self.id = "rs_1"

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Return the fields accepted as a Responses input reasoning item."""
        return {
            "type": "reasoning",
            "id": self.id,
            "summary": [],
            "encrypted_content": "encrypted",
            "status": "completed",
        }


def _event(event_type: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **kwargs)


def _completed_event() -> SimpleNamespace:
    return _event(
        "response.completed",
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=5)
        ),
    )


@pytest.mark.parametrize(
    ("configured_mode", "model", "expected"),
    [
        (API_MODE_AUTO, "gpt-5.4-mini", API_MODE_CHAT_COMPLETIONS),
        (API_MODE_AUTO, "gpt-5.6-luna", API_MODE_RESPONSES),
        (API_MODE_AUTO, "gpt-5.10", API_MODE_RESPONSES),
        (API_MODE_CHAT_COMPLETIONS, "gpt-5.6-luna", API_MODE_CHAT_COMPLETIONS),
        (API_MODE_RESPONSES, "gpt-4.1-mini", API_MODE_RESPONSES),
    ],
)
def test_get_api_mode(configured_mode: str, model: str, expected: str) -> None:
    """Auto is conservative and explicit modes always win."""
    assert get_api_mode(configured_mode, model) == expected


def test_responses_history_and_tools_mapping() -> None:
    """Tool calls and outputs use Responses input item types."""
    tool_input = SimpleNamespace(
        id="call_1", tool_name="turn_on", tool_args={"entity_id": "light.kitchen"}
    )
    history = [
        conversation.SystemContent(content="Be helpful"),
        conversation.UserContent(content="Turn on the kitchen"),
        conversation.AssistantContent(
            agent_id="agent.test", native=FakeReasoningItem()
        ),
        conversation.AssistantContent(agent_id="agent.test", tool_calls=[tool_input]),
        conversation.ToolResultContent(
            agent_id="agent.test",
            tool_call_id="call_1",
            tool_name="turn_on",
            tool_result={"result": "done"},
        ),
    ]

    result = _convert_content_to_responses_param(history)

    assert [item["type"] for item in result] == [
        "message",
        "message",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert result[2] == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "encrypted",
    }
    assert result[3]["call_id"] == "call_1"
    assert result[4]["call_id"] == "call_1"

    function_tools = [
        {
            "spec": {
                "name": "turn_on",
                "description": "Turn on an entity",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    ]
    assert _format_tools(function_tools, API_MODE_RESPONSES)[0] == {
        "type": "function",
        **function_tools[0]["spec"],
    }
    assert (
        _format_tools(function_tools, API_MODE_CHAT_COMPLETIONS)[0]["function"]
        == function_tools[0]["spec"]
    )


@pytest.mark.parametrize("reasoning_effort", ["low", "medium", "high"])
async def test_responses_tool_chain_preserves_reasoning(
    hass, reasoning_effort: str
) -> None:
    """A Responses tool result is fed into a second streamed request."""
    reasoning_item = FakeReasoningItem()
    function_call = SimpleNamespace(
        type="function_call",
        call_id="call_1",
        name="get_state",
        arguments='{"entity_id":"light.kitchen"}',
    )
    message_item = SimpleNamespace(type="message")

    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=reasoning_item),
                            _event("response.output_item.done", item=reasoning_item),
                            _event("response.output_item.added", item=function_call),
                            _event("response.output_item.done", item=function_call),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=message_item),
                            _event(
                                "response.output_text.delta", delta="The light is on."
                            ),
                            _event("response.output_item.done", item=message_item),
                            _completed_event(),
                        ]
                    ),
                ]
            )
        )
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(
        data={
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_AUTO,
            CONF_REASONING_EFFORT: reasoning_effort,
        }
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._execute_function_tool = AsyncMock(
        return_value=conversation.ToolResultContent(
            agent_id=entity.entity_id,
            tool_call_id="call_1",
            tool_name="get_state",
            tool_result={"result": "on"},
        )
    )

    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_user_content(conversation.UserContent(content="Is it on?"))
    function_tools = [
        {
            "spec": {
                "name": "get_state",
                "description": "Get an entity state",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "unused-in-test"},
        }
    ]

    await entity._async_handle_chat_log(chat_log, function_tools, [])

    assert client.responses.create.await_count == 2
    first_request = client.responses.create.await_args_list[0].kwargs
    assert first_request["reasoning"] == {"effort": reasoning_effort}
    assert first_request["store"] is False
    assert first_request["tools"][0]["name"] == "get_state"

    second_input = client.responses.create.await_args_list[1].kwargs["input"]
    assert any(item["type"] == "reasoning" for item in second_input)
    assert any(item["type"] == "function_call" for item in second_input)
    assert any(item["type"] == "function_call_output" for item in second_input)
    assert chat_log.content[-1].content == "The light is on."


async def test_responses_stream_supports_multiple_tool_calls() -> None:
    """Parallel function-call output items become separate HA tool inputs."""
    calls = [
        SimpleNamespace(
            type="function_call",
            call_id=f"call_{index}",
            name="get_state",
            arguments=f'{{"entity_id":"light.room_{index}"}}',
        )
        for index in (1, 2)
    ]
    stream = FakeStream(
        [
            event
            for call in calls
            for event in (
                _event("response.output_item.added", item=call),
                _event("response.output_item.done", item=call),
            )
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)

    deltas = [
        delta
        async for delta in entity._transform_responses_stream(SimpleNamespace(), stream)
    ]
    tool_calls = [
        tool_call for delta in deltas for tool_call in delta.get("tool_calls", [])
    ]

    assert [tool_call.id for tool_call in tool_calls] == ["call_1", "call_2"]
    assert all(tool_call.external for tool_call in tool_calls)


async def test_responses_image_attachment(hass, tmp_path) -> None:
    """Image attachments are encoded as Responses input_image content."""
    image_path = tmp_path / "camera.png"
    image_path.write_bytes(b"image-data")
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(
            content="What is shown?",
            attachments=[
                conversation.Attachment(
                    media_content_id="media-source://camera/test",
                    mime_type="image/png",
                    path=image_path,
                )
            ],
        )
    )
    messages = _convert_content_to_responses_param(chat_log.content)
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass

    await entity._async_add_attachments(chat_log, messages, API_MODE_RESPONSES)

    assert messages[-1]["content"][0] == {
        "type": "input_text",
        "text": "What is shown?",
    }
    assert messages[-1]["content"][1]["type"] == "input_image"
    assert messages[-1]["content"][1]["image_url"].startswith("data:image/png;base64,")
