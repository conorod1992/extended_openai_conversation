"""Usage accounting coverage for correctable Function Tool recovery."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOL_ERROR_RECOVERY,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)


class FakeStream:
    """Async iterator over fake Responses API events."""

    def __init__(self, events: list[Any]) -> None:
        self.events = events

    async def __aiter__(self) -> AsyncIterator[Any]:
        for event in self.events:
            yield event


class UsageProbe:
    """Record provider-request accounting without involving persistent stores."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._run = object()

    def current_run(self) -> object:
        return self._run

    async def async_record_request(self, **kwargs: Any) -> None:
        self.requests.append(kwargs)


def _event(event_type: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **kwargs)


def _completed_event(input_tokens: int, output_tokens: int) -> SimpleNamespace:
    return _event(
        "response.completed",
        response=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        ),
    )


def _invalid_tool_stream() -> FakeStream:
    item = SimpleNamespace(
        type="function_call",
        call_id="invalid-1",
        name="set_mode",
        arguments=json.dumps({}),
    )
    return FakeStream(
        [
            _event("response.output_item.added", item=item),
            _event("response.output_item.done", item=item),
            _completed_event(11, 3),
        ]
    )


def _final_stream() -> FakeStream:
    return FakeStream(
        [
            _event(
                "response.output_item.added",
                item=SimpleNamespace(type="message"),
            ),
            _event("response.output_text.delta", delta="Done"),
            _completed_event(17, 4),
        ]
    )


def _tool() -> dict[str, Any]:
    return {
        "spec": {
            "name": "set_mode",
            "description": "Set a test mode",
            "parameters": {
                "type": "object",
                "properties": {"mode": {"type": "string"}},
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
        "function": {"type": "service", "service": "test.set_mode"},
    }


def _chat_log(hass: Any) -> conversation.ChatLog:
    chat_log = conversation.ChatLog(hass, "recovery-usage")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_user_content(conversation.UserContent(content="Set the mode"))
    return chat_log


async def test_recovery_provider_round_is_accounted_once(hass) -> None:
    """A correction round records two provider requests without inventing a tool run."""
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(side_effect=[_invalid_tool_stream(), _final_stream()])
        )
    )
    usage = UsageProbe()
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        runtime_data=client,
        data={},
        entry_id="entry-1",
    )
    entity.subentry = SimpleNamespace(
        subentry_id="agent-1",
        data={
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION: 2,
            CONF_FUNCTION_TOOL_ERROR_RECOVERY: True,
        },
    )
    entity.hass = hass
    entity.entity_id = "conversation.recovery_usage"
    entity._usage = usage
    entity._execute_function_tool = AsyncMock()

    await entity._async_handle_chat_log(_chat_log(hass), [_tool()], [])

    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_not_awaited()

    assert len(usage.requests) == 2
    assert [request["successful"] for request in usage.requests] == [True, True]
    assert [request["request_stage"] for request in usage.requests] == [
        "initial",
        "after_tool",
    ]
    assert [request["tool_calls_requested"] for request in usage.requests] == [1, 0]

    first_usage = usage.requests[0]["usage"]
    second_usage = usage.requests[1]["usage"]
    assert (first_usage.input_tokens, first_usage.output_tokens) == (11, 3)
    assert (second_usage.input_tokens, second_usage.output_tokens) == (17, 4)
