"""Tests for Chat Completions and Responses API adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_AUTO,
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    CONF_WEB_SEARCH,
    CONF_WEB_SEARCH_CONTEXT,
    CONTINUE_CONVERSATION_ALWAYS,
    CONTINUE_CONVERSATION_CONDITIONAL,
    DEFAULT_CONTINUE_CONVERSATION,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _get_continue_conversation_mode,
    _resolve_continue_conversation,
)
from custom_components.extended_openai_conversation_responses.entity import (
    CONTINUE_CONVERSATION_TOOL_NAME,
    ExtendedOpenAIBaseLLMEntity,
    _build_web_search_tool,
    _convert_content_to_responses_param,
    _format_tools,
)
from custom_components.extended_openai_conversation_responses.helpers import (
    get_api_mode,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError


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


class FakeWebSearchItem:
    """Minimal serializable hosted Web Search response item."""

    type = "web_search_call"

    def __init__(self) -> None:
        self.id = "ws_1"
        self.status = "completed"

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Return a Responses input-compatible hosted-tool item."""
        return {
            "type": self.type,
            "id": self.id,
            "status": self.status,
            "action": {"type": "search", "query": "weather in Dublin"},
        }


class FakeCitedMessageItem:
    """Minimal serializable message containing a URL citation annotation."""

    type = "message"

    def __init__(self) -> None:
        self.id = "msg_1"
        self.content = [SimpleNamespace(type="output_text")]

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Return the message and its structured citation annotation."""
        return {
            "type": self.type,
            "id": self.id,
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "It is mild today.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "start_index": 0,
                            "end_index": 18,
                            "title": "Forecast",
                            "url": "https://example.com/forecast",
                        }
                    ],
                }
            ],
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


def _function_call(
    name: str, arguments: str, call_id: str = "call_1"
) -> SimpleNamespace:
    """Create a Responses API function-call output item."""
    return SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
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


def test_web_search_defaults_off() -> None:
    """Existing agents do not expose the hosted tool without the new option."""
    assert _build_web_search_tool({}, API_MODE_RESPONSES, {}) is None


@pytest.mark.parametrize("context_size", ["low", "medium", "high"])
def test_web_search_tool_schema(context_size: str) -> None:
    """Responses receives the native hosted-tool schema and selected context."""
    assert _build_web_search_tool(
        {
            CONF_WEB_SEARCH: True,
            CONF_WEB_SEARCH_CONTEXT: context_size,
        },
        API_MODE_RESPONSES,
        {
            CONF_API_PROVIDER: "openai",
            CONF_BASE_URL: "https://api.openai.com/v1",
        },
    ) == {
        "type": "web_search",
        "search_context_size": context_size,
    }


@pytest.mark.parametrize(
    ("api_mode", "entry_data", "message"),
    [
        (API_MODE_CHAT_COMPLETIONS, {}, "requires the Responses API"),
        (
            API_MODE_RESPONSES,
            {CONF_API_PROVIDER: "azure"},
            "direct OpenAI Responses API",
        ),
        (
            API_MODE_RESPONSES,
            {CONF_BASE_URL: "https://example.com/v1"},
            "direct OpenAI Responses API",
        ),
    ],
)
def test_web_search_rejects_unsupported_api_configuration(
    api_mode: str, entry_data: dict[str, Any], message: str
) -> None:
    """Hosted Web Search is never sent to unsupported API endpoints."""
    with pytest.raises(HomeAssistantError, match=message):
        _build_web_search_tool({CONF_WEB_SEARCH: True}, api_mode, entry_data)


def test_responses_native_search_and_citations_are_replayed() -> None:
    """Hosted search state and citation annotations survive stateless rounds."""
    search_item = FakeWebSearchItem()
    message_item = FakeCitedMessageItem()
    history = [
        conversation.AssistantContent(agent_id="agent.test", native=search_item),
        conversation.AssistantContent(
            agent_id="agent.test",
            content="It is mild today.",
            native=message_item,
        ),
    ]

    result = _convert_content_to_responses_param(history)

    assert result[0] == search_item.model_dump()
    assert result[1] == message_item.model_dump()
    assert result[1]["content"][0]["annotations"][0]["type"] == "url_citation"
    assert len(result) == 2


async def test_responses_stream_separates_native_output_items(hass) -> None:
    """Each native Responses output item gets its own HA content entry."""
    reasoning_item = FakeReasoningItem()
    search_item = FakeWebSearchItem()
    message_item = FakeCitedMessageItem()
    stream = FakeStream(
        [
            _event("response.output_item.added", item=reasoning_item),
            _event("response.output_item.done", item=reasoning_item),
            _event("response.output_item.added", item=search_item),
            _event("response.output_item.done", item=search_item),
            _event("response.output_item.added", item=message_item),
            _event("response.output_text.delta", delta="It is mild today."),
            _event("response.output_item.done", item=message_item),
            _completed_event(),
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(data={})
    chat_log = conversation.ChatLog(hass, "conversation-id")

    contents = [
        content
        async for content in chat_log.async_add_delta_content_stream(
            "conversation.test",
            entity._transform_responses_stream(chat_log, stream),
        )
    ]

    assert [content.native.type for content in contents] == [
        "reasoning",
        "web_search_call",
        "message",
    ]
    assert contents[-1].content == "It is mild today."


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
    entity._usage = SimpleNamespace(
        async_record_conversation=AsyncMock(),
        async_record_request=AsyncMock(),
    )
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
    entity._usage.async_record_conversation.assert_awaited_once()
    assert entity._usage.async_record_request.await_count == 2
    assert all(
        call.kwargs["successful"] is True and call.kwargs["usage"].total_tokens == 15
        for call in entity._usage.async_record_request.await_args_list
    )
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


async def test_responses_stream_preserves_web_search_and_citations() -> None:
    """Hosted-tool progress is silent while completed native items are retained."""
    search_item = FakeWebSearchItem()
    message_item = FakeCitedMessageItem()
    stream = FakeStream(
        [
            _event("response.web_search_call.in_progress", item_id="ws_1"),
            _event("response.web_search_call.searching", item_id="ws_1"),
            _event("response.output_item.added", item=search_item),
            _event("response.output_item.done", item=search_item),
            _event("response.output_item.added", item=message_item),
            _event("response.output_text.delta", delta="It is mild today."),
            _event("response.output_item.done", item=message_item),
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)

    deltas = [
        delta
        async for delta in entity._transform_responses_stream(SimpleNamespace(), stream)
    ]

    assert [delta["native"] for delta in deltas if "native" in delta] == [
        search_item,
        message_item,
    ]
    assert [delta.get("content") for delta in deltas if "content" in delta] == [
        "It is mild today."
    ]
    assert not any(delta.get("tool_calls") for delta in deltas)


async def test_web_search_coexists_with_ha_tool_and_conditional_finalizer(hass) -> None:
    """Search output is replayed before an HA action and final structured answer."""
    search_item = FakeWebSearchItem()
    action_call = _function_call(
        "turn_on", '{"entity_id":"light.kitchen"}', "action_call"
    )
    finalizer = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        '{"response":"The kitchen light is on.","continue_conversation":false}',
        "final_call",
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=search_item),
                            _event("response.output_item.done", item=search_item),
                            _event("response.output_item.added", item=action_call),
                            _event("response.output_item.done", item=action_call),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=finalizer),
                            _event("response.output_item.done", item=finalizer),
                            _completed_event(),
                        ]
                    ),
                ]
            )
        )
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client, data={})
    entity.subentry = SimpleNamespace(
        data={
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_WEB_SEARCH: True,
            CONF_WEB_SEARCH_CONTEXT: "medium",
        }
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._execute_function_tool = AsyncMock(
        return_value=conversation.ToolResultContent(
            agent_id=entity.entity_id,
            tool_call_id="action_call",
            tool_name="turn_on",
            tool_result={"result": "done"},
        )
    )
    function_tools = [
        {
            "spec": {
                "name": "turn_on",
                "description": "Turn on an entity",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "unused-in-test"},
        }
    ]
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(content="Check the weather, then turn on the light")
    )

    result = await entity._async_handle_chat_log(
        chat_log, function_tools, [], conditional_continue=True
    )

    assert result is False
    assert client.responses.create.await_count == 2
    first_request = client.responses.create.await_args_list[0].kwargs
    assert first_request["tool_choice"] == "required"
    assert first_request["tools"][0] == {
        "type": "web_search",
        "search_context_size": "medium",
    }
    assert {tool["name"] for tool in first_request["tools"][1:]} == {
        "turn_on",
        CONTINUE_CONVERSATION_TOOL_NAME,
    }
    second_input = client.responses.create.await_args_list[1].kwargs["input"]
    assert any(item["type"] == "web_search_call" for item in second_input)
    assert any(item["type"] == "function_call" for item in second_input)
    assert any(item["type"] == "function_call_output" for item in second_input)
    assert chat_log.content[-1].content == "The kitchen light is on."


@pytest.mark.parametrize("decision", [False, True])
async def test_conditional_continue_uses_structured_finalizer(
    hass, decision: bool
) -> None:
    """Conditional mode consumes structured control without another request."""
    finalizer = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        json.dumps(
            {
                "response": "Which room did you mean?",
                "continue_conversation": decision,
            }
        ),
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=FakeStream(
                    [
                        _event("response.output_item.added", item=finalizer),
                        _event("response.output_item.done", item=finalizer),
                        _completed_event(),
                    ]
                )
            )
        )
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"

    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(conversation.UserContent(content="Help me"))

    result = await entity._async_handle_chat_log(
        chat_log, [], [], conditional_continue=True
    )

    assert result is decision
    assert chat_log.content[-1].content == "Which room did you mean?"
    assert chat_log.content[-1].tool_calls is None
    assert client.responses.create.await_count == 1
    request = client.responses.create.await_args.kwargs
    assert request["tool_choice"] == "required"
    assert request["tools"][0]["name"] == CONTINUE_CONVERSATION_TOOL_NAME


async def test_conditional_continue_after_home_assistant_tool(hass) -> None:
    """The structured decision is applied after the normal tool flow completes."""
    action_call = _function_call(
        "turn_on", '{"entity_id":"light.kitchen"}', "action_call"
    )
    finalizer = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        '{"response":"The kitchen light is on.","continue_conversation":false}',
        "final_call",
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=action_call),
                            _event("response.output_item.done", item=action_call),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=finalizer),
                            _event("response.output_item.done", item=finalizer),
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
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._execute_function_tool = AsyncMock(
        return_value=conversation.ToolResultContent(
            agent_id=entity.entity_id,
            tool_call_id="action_call",
            tool_name="turn_on",
            tool_result={"result": "done"},
        )
    )
    function_tools = [
        {
            "spec": {
                "name": "turn_on",
                "description": "Turn on an entity",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "unused-in-test"},
        }
    ]
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(content="Turn on the kitchen light")
    )

    result = await entity._async_handle_chat_log(
        chat_log, function_tools, [], conditional_continue=True
    )

    assert result is False
    assert client.responses.create.await_count == 2
    assert entity._execute_function_tool.await_count == 1
    assert chat_log.content[-1].content == "The kitchen light is on."
    for request in client.responses.create.await_args_list:
        assert request.kwargs["tool_choice"] == "required"
        assert {tool["name"] for tool in request.kwargs["tools"]} == {
            "turn_on",
            CONTINUE_CONVERSATION_TOOL_NAME,
        }


async def test_conditional_continue_allows_multiple_tools_before_finalizer(
    hass,
) -> None:
    """Conditional mode keeps action tools available until work is complete."""
    first_action = _function_call(
        "turn_on", '{"entity_id":"light.kitchen"}', "first_action"
    )
    second_action = _function_call(
        "get_state", '{"entity_id":"lock.back_door"}', "second_action"
    )
    finalizer = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        '{"response":"The light is on and the back door is locked.",'
        '"continue_conversation":false}',
        "final_call",
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=first_action),
                            _event("response.output_item.done", item=first_action),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=second_action),
                            _event("response.output_item.done", item=second_action),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=finalizer),
                            _event("response.output_item.done", item=finalizer),
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
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._execute_function_tool = AsyncMock(
        side_effect=[
            conversation.ToolResultContent(
                agent_id=entity.entity_id,
                tool_call_id="first_action",
                tool_name="turn_on",
                tool_result={"result": "done"},
            ),
            conversation.ToolResultContent(
                agent_id=entity.entity_id,
                tool_call_id="second_action",
                tool_name="get_state",
                tool_result={"result": "locked"},
            ),
        ]
    )
    function_tools = [
        {
            "spec": {
                "name": name,
                "description": f"Run {name}",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "unused-in-test"},
        }
        for name in ("turn_on", "get_state")
    ]
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(
            content="Turn on the kitchen light and check the back door"
        )
    )

    result = await entity._async_handle_chat_log(
        chat_log, function_tools, [], conditional_continue=True
    )

    assert result is False
    assert client.responses.create.await_count == 3
    assert entity._execute_function_tool.await_count == 2
    assert all(
        request.kwargs["tool_choice"] == "required"
        for request in client.responses.create.await_args_list
    )
    assert chat_log.content[-1].content == (
        "The light is on and the back door is locked."
    )


async def test_conditional_continue_retries_ordinary_text_with_finalizer(hass) -> None:
    """Unexpected ordinary text gets one finalizer-only continuation request."""
    message_item = SimpleNamespace(type="message")
    finalizer = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        '{"response":"Which room did you mean?","continue_conversation":true}',
        "final_call",
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=message_item),
                            _event(
                                "response.output_text.delta",
                                delta="Draft answer without a finalizer.",
                            ),
                            _event("response.output_item.done", item=message_item),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=finalizer),
                            _event("response.output_item.done", item=finalizer),
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
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(conversation.UserContent(content="Help me"))

    result = await entity._async_handle_chat_log(
        chat_log, [], [], conditional_continue=True
    )

    assert result is True
    assert client.responses.create.await_count == 2
    retry = client.responses.create.await_args_list[1].kwargs
    assert retry["tool_choice"] == "required"
    assert [tool["name"] for tool in retry["tools"]] == [
        CONTINUE_CONVERSATION_TOOL_NAME
    ]
    assert any(
        item.get("content") == "Draft answer without a finalizer."
        for item in retry["input"]
    )
    assert all(
        content.content != "Draft answer without a finalizer."
        for content in chat_log.content
    )
    assert chat_log.content[-1].content == "Which room did you mean?"


async def test_conditional_continue_finalization_retry_is_bounded(hass) -> None:
    """A provider that ignores required tool choice gets only one retry."""
    first_message = SimpleNamespace(type="message")
    second_message = SimpleNamespace(type="message")
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=first_message),
                            _event(
                                "response.output_text.delta",
                                delta="First unstructured draft.",
                            ),
                            _event("response.output_item.done", item=first_message),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=second_message),
                            _event(
                                "response.output_text.delta",
                                delta="Usable fallback response.",
                            ),
                            _event("response.output_item.done", item=second_message),
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
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(conversation.UserContent(content="Help me"))

    result = await entity._async_handle_chat_log(
        chat_log, [], [], conditional_continue=True
    )

    assert result is None
    assert client.responses.create.await_count == 2
    assert all(
        getattr(content, "content", None) != "First unstructured draft."
        for content in chat_log.content
    )
    assert chat_log.content[-1].content == "Usable fallback response."


async def test_conditional_continue_discards_premature_finalizer(hass) -> None:
    """A finalizer beside an action does not end the tool workflow."""
    action = _function_call("turn_on", '{"entity_id":"light.kitchen"}', "action_call")
    premature = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        '{"response":"I will turn it on.","continue_conversation":false}',
        "premature_call",
    )
    finalizer = _function_call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        '{"response":"The kitchen light is on.","continue_conversation":false}',
        "final_call",
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=[
                    FakeStream(
                        [
                            _event("response.output_item.added", item=action),
                            _event("response.output_item.done", item=action),
                            _event("response.output_item.added", item=premature),
                            _event("response.output_item.done", item=premature),
                            _completed_event(),
                        ]
                    ),
                    FakeStream(
                        [
                            _event("response.output_item.added", item=finalizer),
                            _event("response.output_item.done", item=finalizer),
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
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._execute_function_tool = AsyncMock(
        return_value=conversation.ToolResultContent(
            agent_id=entity.entity_id,
            tool_call_id="action_call",
            tool_name="turn_on",
            tool_result={"result": "done"},
        )
    )
    function_tools = [
        {
            "spec": {
                "name": "turn_on",
                "description": "Turn on an entity",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "unused-in-test"},
        }
    ]
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(content="Turn on the kitchen light")
    )

    result = await entity._async_handle_chat_log(
        chat_log, function_tools, [], conditional_continue=True
    )

    assert result is False
    assert client.responses.create.await_count == 2
    assert entity._execute_function_tool.await_count == 1
    assert all(
        getattr(content, "content", None) != "I will turn it on."
        for content in chat_log.content
    )
    assert chat_log.content[-1].content == "The kitchen light is on."


async def test_conditional_continue_rejects_tool_name_collision(hass) -> None:
    """A user tool cannot collide with the internal finalizer name."""
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    function_tools = [
        {
            "spec": {
                "name": CONTINUE_CONVERSATION_TOOL_NAME,
                "description": "Conflicting user tool",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "unused-in-test"},
        }
    ]
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(conversation.UserContent(content="Help me"))

    with pytest.raises(HomeAssistantError, match="reserved"):
        await entity._async_handle_chat_log(
            chat_log, function_tools, [], conditional_continue=True
        )

    client.responses.create.assert_not_awaited()


@pytest.mark.parametrize(
    ("mode", "ha_default", "decision", "expected"),
    [
        (DEFAULT_CONTINUE_CONVERSATION, False, None, False),
        (DEFAULT_CONTINUE_CONVERSATION, True, None, True),
        (CONTINUE_CONVERSATION_ALWAYS, False, None, True),
        (CONTINUE_CONVERSATION_CONDITIONAL, True, False, False),
        (CONTINUE_CONVERSATION_CONDITIONAL, False, True, True),
    ],
)
def test_resolve_continue_conversation(
    mode: str, ha_default: bool, decision: bool | None, expected: bool
) -> None:
    """Each configured mode resolves independently of spoken punctuation."""
    assert _resolve_continue_conversation(mode, ha_default, decision) is expected


def test_missing_continue_conversation_option_uses_ha_default() -> None:
    """Existing config entries need no migration to preserve prior behavior."""
    assert _get_continue_conversation_mode({}) == DEFAULT_CONTINUE_CONVERSATION


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


async def test_failed_api_request_is_forwarded_to_usage_statistics(hass) -> None:
    """Request creation failures increment the per-agent failure counter."""
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=HomeAssistantError("provider rejected request")
            )
        )
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client)
    entity.subentry = SimpleNamespace(
        data={CONF_CHAT_MODEL: "gpt-5.6-luna", CONF_API_MODE: API_MODE_RESPONSES}
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._usage = SimpleNamespace(
        async_record_conversation=AsyncMock(),
        async_record_request=AsyncMock(),
    )
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_user_content(conversation.UserContent(content="Hello"))

    with pytest.raises(HomeAssistantError, match="provider rejected"):
        await entity._async_handle_chat_log(chat_log, [], [])

    entity._usage.async_record_conversation.assert_awaited_once()
    entity._usage.async_record_request.assert_awaited_once()
    assert entity._usage.async_record_request.await_args.kwargs["successful"] is False
