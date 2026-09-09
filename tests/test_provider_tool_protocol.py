"""Provider-round protocol integrity regressions for Function Tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_param,
)


class _FakeStream:
    """Deterministic async stream accepted by the Chat Completions transformer."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[Any]:
        for chunk in self._chunks:
            yield chunk


def _chat_chunk(
    *,
    content: str | None = None,
    finish_reason: str | None = None,
    tool_calls: list[Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def _tool_call_stream(
    call_id: str, name: str, arguments: dict[str, Any]
) -> _FakeStream:
    tool_delta = SimpleNamespace(
        index=0,
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(arguments, separators=(",", ":")),
        ),
    )
    return _FakeStream(
        [_chat_chunk(tool_calls=[tool_delta], finish_reason="tool_calls")]
    )


def _final_stream(text: str = "Done") -> _FakeStream:
    return _FakeStream([_chat_chunk(content=text, finish_reason="stop")])


def _tool(name: str) -> dict[str, Any]:
    return {
        "spec": {
            "name": name,
            "description": f"Test tool {name}",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string"},
                    "value": {"type": "integer"},
                },
            },
        },
        "function": {"type": "native", "name": "unused-in-test"},
    }


def _entity(hass: Any, streams: list[Any]) -> Any:
    create = AsyncMock(side_effect=streams)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(runtime_data=client, data={})
    entity.subentry = SimpleNamespace(
        data={
            CONF_CHAT_MODEL: "gpt-4.1-mini",
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
        }
    )
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity._usage = None
    return entity


def _chat_log(hass: Any) -> conversation.ChatLog:
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_user_content(conversation.UserContent(content="Do the work"))
    return chat_log


def _result(entity: Any, tool_input: Any, value: Any) -> conversation.ToolResultContent:
    return conversation.ToolResultContent(
        agent_id=entity.entity_id,
        tool_call_id=tool_input.id,
        tool_name=tool_input.tool_name,
        tool_result={"result": value},
    )


def _protocol_messages(messages: list[dict[str, Any]]) -> tuple[list[Any], list[Any]]:
    calls = [
        tool_call
        for message in messages
        if message.get("role") == "assistant"
        for tool_call in message.get("tool_calls", [])
    ]
    outputs = [message for message in messages if message.get("role") == "tool"]
    return calls, outputs


async def test_chat_completions_tool_result_is_paired_on_following_request(hass) -> None:
    """A streamed tool call is executed once and replayed with its matching output."""
    entity = _entity(
        hass,
        [
            _tool_call_stream(
                "call-1", "get_state", {"entity_id": "light.kitchen"}
            ),
            _final_stream("The kitchen light is on."),
        ],
    )
    executed: list[tuple[str, str, dict[str, Any]]] = []

    async def execute(
        _function_tool: dict[str, Any],
        tool_input: Any,
        _llm_context: Any,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        executed.append((tool_input.id, tool_input.tool_name, tool_input.tool_args))
        return _result(entity, tool_input, "on")

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    chat_log = _chat_log(hass)

    await entity._async_handle_chat_log(chat_log, [_tool("get_state")], [])

    assert executed == [
        ("call-1", "get_state", {"entity_id": "light.kitchen"})
    ]
    create = entity._client.chat.completions.create
    assert create.await_count == 2
    second_messages = create.await_args_list[1].kwargs["messages"]
    calls, outputs = _protocol_messages(second_messages)
    assert [call["id"] for call in calls] == ["call-1"]
    assert [output["tool_call_id"] for output in outputs] == ["call-1"]
    assert json.loads(outputs[0]["content"]) == {"result": "on"}
    assert chat_log.content[-1].content == "The kitchen light is on."


async def test_chat_completions_sequential_tool_rounds_preserve_ids_and_order(hass) -> None:
    """Two provider rounds retain one ordered call/output pair for each side effect."""
    entity = _entity(
        hass,
        [
            _tool_call_stream("call-1", "first", {"value": 1}),
            _tool_call_stream("call-2", "second", {"value": 2}),
            _final_stream("Both actions completed."),
        ],
    )
    executed: list[str] = []

    async def execute(
        _function_tool: dict[str, Any],
        tool_input: Any,
        _llm_context: Any,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        executed.append(tool_input.id)
        return _result(entity, tool_input, f"{tool_input.tool_name}-ok")

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    chat_log = _chat_log(hass)

    await entity._async_handle_chat_log(
        chat_log, [_tool("first"), _tool("second")], []
    )

    assert executed == ["call-1", "call-2"]
    create = entity._client.chat.completions.create
    assert create.await_count == 3
    third_messages = create.await_args_list[2].kwargs["messages"]
    calls, outputs = _protocol_messages(third_messages)
    assert [call["id"] for call in calls] == ["call-1", "call-2"]
    assert [output["tool_call_id"] for output in outputs] == ["call-1", "call-2"]

    call_positions = [
        index
        for index, message in enumerate(third_messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    output_positions = [
        index
        for index, message in enumerate(third_messages)
        if message.get("role") == "tool"
    ]
    assert call_positions[0] < output_positions[0] < call_positions[1] < output_positions[1]
    assert chat_log.content[-1].content == "Both actions completed."


async def test_chat_completions_tool_failure_closes_retained_call(hass) -> None:
    """A local execution failure leaves a matched error output instead of an orphan."""
    entity = _entity(hass, [_tool_call_stream("call-1", "first", {"value": 1})])
    entity._execute_function_tool = AsyncMock(
        side_effect=HomeAssistantError("service failed")
    )
    chat_log = _chat_log(hass)

    with pytest.raises(HomeAssistantError, match="service failed"):
        await entity._async_handle_chat_log(chat_log, [_tool("first")], [])

    entity._execute_function_tool.assert_awaited_once()
    history = _convert_content_to_param(chat_log.content)
    calls, outputs = _protocol_messages(history)
    assert [call["id"] for call in calls] == ["call-1"]
    assert [output["tool_call_id"] for output in outputs] == ["call-1"]
    error_result = json.loads(outputs[0]["content"])["result"]
    assert error_result["status"] == "error"
    assert "service failed" in error_result["error"]


async def test_chat_completions_provider_failure_after_tool_does_not_retry_side_effect(
    hass,
) -> None:
    """A failed follow-up provider request preserves the completed tool exchange."""
    entity = _entity(
        hass,
        [
            _tool_call_stream("call-1", "first", {"value": 1}),
            HomeAssistantError("provider rejected follow-up"),
        ],
    )
    executed = 0

    async def execute(
        _function_tool: dict[str, Any],
        tool_input: Any,
        _llm_context: Any,
        _exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        nonlocal executed
        executed += 1
        return _result(entity, tool_input, "done")

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    chat_log = _chat_log(hass)

    with pytest.raises(HomeAssistantError, match="provider rejected follow-up"):
        await entity._async_handle_chat_log(chat_log, [_tool("first")], [])

    assert executed == 1
    create = entity._client.chat.completions.create
    assert create.await_count == 2
    history = _convert_content_to_param(chat_log.content)
    calls, outputs = _protocol_messages(history)
    assert [call["id"] for call in calls] == ["call-1"]
    assert [output["tool_call_id"] for output in outputs] == ["call-1"]
    assert json.loads(outputs[0]["content"]) == {"result": "done"}
