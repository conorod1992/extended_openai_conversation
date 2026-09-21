"""Focused branch coverage for the base conversation entity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import entity as module
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONTEXT_TRUNCATE_SUMMARIZE,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm


class _Stream:
    """Small async stream used by both provider adapters."""

    def __init__(self, events: list[Any]) -> None:
        self._events = iter(events)

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._events)
        except StopIteration as err:
            raise StopAsyncIteration from err


def _entity() -> ExtendedOpenAIBaseLLMEntity:
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(data={})
    entity.entry = SimpleNamespace(runtime_data=None, data={})
    entity._usage = None
    return entity


async def _collect(generator: Any) -> list[Any]:
    return [item async for item in generator]


def test_schema_and_chat_conversion_defensive_branches() -> None:
    """Malformed schema containers and truthy empty calls remain harmless."""
    object_schema = {"type": "object", "properties": "invalid"}
    array_schema = {"type": "array", "items": "invalid"}
    module._adjust_schema(object_schema)
    module._adjust_schema(array_schema)
    assert object_schema["properties"] == "invalid"
    assert array_schema["items"] == "invalid"

    class TruthyEmpty(list[Any]):
        def __bool__(self) -> bool:
            return True

    messages = module._convert_content_to_param(
        [SimpleNamespace(role="assistant", content=None, tool_calls=TruthyEmpty())]
    )
    assert messages == [{"role": "assistant"}]


async def test_chat_stream_usage_coercion_and_partial_tool_deltas() -> None:
    """Usage-only chunks and unusual provider delta values are normalized."""
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
    trace = SimpleNamespace(async_trace=Mock())
    request_usage = RequestUsage()

    class TruthyBlank:
        def __bool__(self) -> bool:
            return True

        def __str__(self) -> str:
            return ""

    tool_deltas = [
        SimpleNamespace(index=0, id="call-1", function=None),
        SimpleNamespace(
            index=0, id=None, function=SimpleNamespace(name="tool", arguments=None)
        ),
        SimpleNamespace(
            index=0, id=None, function=SimpleNamespace(name=None, arguments="{}")
        ),
    ]
    chunks = [
        SimpleNamespace(choices=[], usage=None),
        SimpleNamespace(choices=[], usage=usage),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=TruthyBlank(), refusal=TruthyBlank(), tool_calls=None
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content={"unexpected": True},
                        refusal=123,
                        tool_calls=tool_deltas,
                    ),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, refusal=None, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        ),
    ]

    deltas = await _collect(
        _entity()._transform_chat_stream(trace, _Stream(chunks), request_usage)
    )

    assert {"content": "{'unexpected': True}"} in deltas
    assert {"content": "123"} in deltas
    assert deltas[-1]["tool_calls"][0].tool_args == {}
    assert request_usage.total_tokens == 5
    trace.async_trace.assert_called_once()


async def test_responses_stream_ignores_empty_events_and_normalizes_metadata() -> None:
    """Responses events cover empty, non-string, and citation variants."""
    valid_citation = SimpleNamespace(
        type="url_citation", start_index=0, end_index=2, title="Docs", url="https://x"
    )
    events = [
        SimpleNamespace(
            type="response.output_item.added", item=SimpleNamespace(type="other")
        ),
        SimpleNamespace(type="response.output_text.delta", delta=""),
        SimpleNamespace(
            type="response.output_text.delta",
            delta="ok",
            output_index=0,
            content_index=0,
        ),
        SimpleNamespace(
            type="response.refusal.delta", delta=123, output_index=1, content_index=0
        ),
        SimpleNamespace(type="response.refusal.delta", delta=""),
        SimpleNamespace(
            type="response.refusal.done", refusal=12345, output_index=1, content_index=0
        ),
        SimpleNamespace(type="response.refusal.done", refusal=None),
        SimpleNamespace(
            type="response.output_text.annotation.added",
            annotation=valid_citation,
            output_index=0,
            content_index=0,
        ),
        SimpleNamespace(
            type="response.output_text.annotation.added",
            annotation=SimpleNamespace(type="other"),
        ),
        SimpleNamespace(
            type="response.completed", response=SimpleNamespace(usage=None)
        ),
    ]

    deltas = await _collect(
        _entity()._transform_responses_stream(SimpleNamespace(), _Stream(events))
    )

    assert deltas == [{"content": "ok"}, {"content": "123"}, {"content": "45"}]


@pytest.mark.parametrize(
    ("api_mode", "format_key"),
    [
        (API_MODE_RESPONSES, "text"),
        (API_MODE_CHAT_COMPLETIONS, "response_format"),
    ],
)
async def test_structured_output_is_sent_in_each_provider_format(
    hass, monkeypatch, api_mode, format_key
) -> None:
    """Structured schemas reach each provider API in its required envelope."""
    response_stream = _Stream(
        [
            SimpleNamespace(
                type="response.output_item.added", item=SimpleNamespace(type="message")
            ),
            SimpleNamespace(type="response.output_text.delta", delta="done"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=10, output_tokens=1)
                ),
            ),
        ]
    )
    chat_stream = _Stream(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="done", refusal=None, tool_calls=None
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            ),
            SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=1, total_tokens=11
                ),
            ),
        ]
    )
    responses_create = AsyncMock(return_value=response_stream)
    chat_create = AsyncMock(return_value=chat_stream)
    entity = _entity()
    entity.hass = hass
    entity.entity_id = "conversation.test"
    entity.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
    )
    entity.subentry.data = {
        CONF_CHAT_MODEL: "gpt-5.6-luna",
        CONF_API_MODE: api_mode,
        CONF_CONTEXT_THRESHOLD: 1,
    }
    entity._truncate_message_history = AsyncMock()
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.content[0] = conversation.SystemContent(content="system")
    chat_log.async_add_user_content(conversation.UserContent(content="question"))
    formatted = Mock(return_value={"type": "object"})
    monkeypatch.setattr(module, "_format_structured_output", formatted)

    await entity._async_handle_chat_log(
        chat_log,
        [],
        [],
        structure_name="Test Result",
        structure=object(),
    )

    create = responses_create if api_mode == API_MODE_RESPONSES else chat_create
    assert format_key in create.await_args.kwargs
    formatted.assert_called_once()
    entity._truncate_message_history.assert_awaited_once()


def test_background_helpers_cover_both_decisions() -> None:
    entity = _entity()
    assert entity.should_run_in_background(None) is False
    assert entity.should_run_in_background({"seconds": 1}) is True


async def test_truncation_fallback_and_summary_success(monkeypatch) -> None:
    """Invalid strategies fall back and a selected history can be summarized."""
    entity = _entity()
    entity.subentry.data = {
        CONF_CONTEXT_TRUNCATE_STRATEGY: "invalid",
        CONF_CONTEXT_THRESHOLD: 10,
    }
    calls: list[tuple[int | None, int]] = []

    def fake_keep(content: list[Any], observed: int | None, threshold: int) -> bool:
        calls.append((observed, threshold))
        return False

    monkeypatch.setattr(module, "keep_recent_messages", fake_keep)
    await entity._truncate_message_history(
        SimpleNamespace(content=[]), observed_input_tokens=20
    )
    assert calls == [(20, 10)]
    system = conversation.SystemContent(content="system")
    older = [conversation.UserContent(content="old")]
    retained = [system, conversation.UserContent(content="new")]
    entity.subentry.data[CONF_CONTEXT_TRUNCATE_STRATEGY] = CONTEXT_TRUNCATE_SUMMARIZE
    monkeypatch.setattr(module, "select_summary_history", lambda *_: (older, retained))
    entity._async_summarize_history = AsyncMock(return_value="durable facts")
    chat_log = SimpleNamespace(content=[system, *older, *retained[1:]])
    await entity._truncate_message_history(chat_log, observed_input_tokens=20)
    assert "durable facts" in chat_log.content[1].content

    monkeypatch.setattr(module, "select_summary_history", lambda *_: None)
    monkeypatch.setattr(module, "keep_recent_messages", lambda *_: True)
    await entity._truncate_message_history(chat_log, observed_input_tokens=20)


async def test_tool_execution_error_and_background_branches(monkeypatch) -> None:
    """HA-tool failures and explicit legacy scheduling take bounded paths."""
    entity = _entity()
    entity.hass = SimpleNamespace()
    entity.entity_id = "conversation.test"
    tool_input = llm.ToolInput(id="call", tool_name="tool", tool_args={})
    ha_tool = {"function": {"reference": "missing"}}
    monkeypatch.setattr(module, "is_ha_tool", lambda _tool: True)
    no_context = await entity._execute_function_tool(ha_tool, tool_input, None, [])
    assert "context unavailable" in str(tool_result_data(no_context))

    monkeypatch.setattr(
        module,
        "current_snapshot",
        lambda: SimpleNamespace(caller_provided=True, tools={}),
    )
    monkeypatch.setattr(module, "reference_key", lambda _reference: "missing")
    unavailable = await entity._execute_function_tool(
        ha_tool, tool_input, SimpleNamespace(context=None), []
    )
    assert "current request" in str(tool_result_data(unavailable))

    monkeypatch.setattr(module, "is_ha_tool", lambda _tool: False)
    monkeypatch.setattr(
        module,
        "async_execution_arguments",
        AsyncMock(return_value={"delay": {"seconds": 1}}),
    )
    function = SimpleNamespace(execute=AsyncMock(return_value="ignored"))
    monkeypatch.setattr(module, "get_function", lambda _type: function)

    from custom_components.extended_openai_conversation_responses.delayed_tools import (
        DATA_DELAYED_TOOL_MANAGER,
        DelayedToolManager,
    )

    manager = object.__new__(DelayedToolManager)
    manager.async_schedule = AsyncMock()
    entity.hass.data = {module.DOMAIN: {DATA_DELAYED_TOOL_MANAGER: manager}}
    scheduled = await entity._execute_function_tool(
        {
            "spec": {
                "name": "tool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delay": {
                            "type": "object",
                            "properties": {"seconds": {"type": "integer"}},
                        }
                    },
                },
            },
            "function": {"type": "template", "template": "work"},
        },
        llm.ToolInput(
            id="scheduled",
            tool_name="tool",
            tool_args={"delay": {"seconds": 1}},
        ),
        None,
        [],
    )
    assert tool_result_data(scheduled) == {"result": "Scheduled"}

    monkeypatch.setattr(
        module,
        "async_execution_arguments",
        AsyncMock(side_effect=HomeAssistantError("strict failure")),
    )
    monkeypatch.setattr(module, "strict_execution_failures_enabled", lambda: True)
    with pytest.raises(HomeAssistantError, match="strict failure"):
        await entity._execute_function_tool(
            {"spec": {}, "function": {"type": "template"}}, tool_input, None, []
        )


@pytest.mark.parametrize(
    ("modern", "token_key"),
    [(True, "max_completion_tokens"), (False, "max_tokens")],
)
async def test_chat_history_summary_token_parameter(
    monkeypatch, modern, token_key
) -> None:
    """Chat summary requests use the model-compatible output-token parameter."""
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=" summary "))],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    entity = _entity()
    entity.entry.runtime_data = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(
        module,
        "get_model_config",
        lambda _model: {"supports_max_completion_tokens": modern},
    )

    result = await entity._async_summarize_history(
        [conversation.UserContent(content="remember this")], "model", "chat"
    )

    assert result == "summary"
    assert create.await_args.kwargs[token_key] == 256


async def test_summary_empty_failure_usage_and_cancellation() -> None:
    """Summary no-op, ordinary failure, and cancellation retain their contracts."""
    entity = _entity()
    assert (
        await entity._async_summarize_history([], "model", API_MODE_RESPONSES) is None
    )

    usage = SimpleNamespace(async_record_request=AsyncMock())
    entity._usage = usage
    entity.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("offline")))
    )
    assert (
        await entity._async_summarize_history(
            [conversation.UserContent(content="old")], "model", API_MODE_RESPONSES
        )
        is None
    )
    assert usage.async_record_request.await_args.kwargs["successful"] is False

    entity.entry.runtime_data.responses.create.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await entity._async_summarize_history(
            [conversation.UserContent(content="old")], "model", API_MODE_RESPONSES
        )


async def test_successful_response_summary_records_usage() -> None:
    usage = SimpleNamespace(async_record_request=AsyncMock())
    entity = _entity()
    entity._usage = usage
    entity.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(output_text="facts", usage=None)
            )
        )
    )
    assert (
        await entity._async_summarize_history(
            [conversation.UserContent(content="old")], "model", API_MODE_RESPONSES
        )
        == "facts"
    )
    assert usage.async_record_request.await_args.kwargs["successful"] is True
