"""Safety and protocol coverage for correctable Function Tool recovery."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.exceptions import (
    FunctionNotFound,
    ParseArgumentsFailed,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    MalformedToolArguments,
    ToolRecoveryState,
    correctable_validation_failure,
    recovery_tool_result,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)


class FakeStream:
    """Small async iterator accepted by both provider stream transformers."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items

    async def __aiter__(self) -> AsyncIterator[Any]:
        for item in self.items:
            yield item


def _chat_log(hass: Any) -> conversation.ChatLog:
    chat_log = conversation.ChatLog(hass, "recovery-test")
    chat_log.content[0] = conversation.SystemContent(content="Be helpful")
    chat_log.async_add_user_content(conversation.UserContent(content="Do it"))
    return chat_log


def _call(call_id: str, name: str, arguments: dict[str, Any] | None = None) -> llm.ToolInput:
    return llm.ToolInput(
        id=call_id,
        tool_name=name,
        tool_args=arguments or {},
        external=True,
    )


def _retain_calls(
    chat_log: conversation.ChatLog, calls: list[llm.ToolInput]
) -> None:
    chat_log.async_add_assistant_content_without_tools(
        conversation.AssistantContent(
            agent_id="conversation.recovery",
            tool_calls=calls,
        )
    )


def _tool(
    name: str,
    *,
    parameters: dict[str, Any] | None = None,
    function_type: str = "service",
    function: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if function is None:
        function = {"type": function_type}
        if function_type == "service":
            function["service"] = f"test.{name}"
    return {
        "spec": {
            "name": name,
            "description": f"Test {name}",
            "parameters": parameters
            or {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "function": function,
    }


def _entity(hass: Any) -> Any:
    return SimpleNamespace(
        hass=hass,
        entity_id="conversation.recovery",
        _execute_function_tool=AsyncMock(),
    )


def _result(entity: Any, tool_input: llm.ToolInput, value: Any = "ok") -> Any:
    return conversation.ToolResultContent(
        agent_id=entity.entity_id,
        tool_call_id=tool_input.id,
        tool_name=tool_input.tool_name,
        tool_result={"result": value},
    )


def _results(chat_log: conversation.ChatLog) -> list[conversation.ToolResultContent]:
    return [
        content
        for content in chat_log.content
        if isinstance(content, conversation.ToolResultContent)
    ]


@pytest.mark.parametrize(
    ("parameters", "arguments", "expected_text"),
    [
        (
            {
                "type": "object",
                "properties": {"entity_id": {"type": "string"}},
                "required": ["entity_id"],
                "additionalProperties": False,
            },
            {},
            "Missing required function input",
        ),
        (
            {
                "type": "object",
                "properties": {"level": {"type": "integer"}},
                "required": ["level"],
                "additionalProperties": False,
            },
            {"level": "not-a-number"},
            "must be integer",
        ),
        (
            {
                "type": "object",
                "properties": {"level": {"type": "integer", "minimum": 1, "maximum": 10}},
                "required": ["level"],
                "additionalProperties": False,
            },
            {"level": 20},
            "must be at most 10",
        ),
        (
            {
                "type": "object",
                "properties": {"mode": {"type": "string", "pattern": "^(on|off)$"}},
                "required": ["mode"],
                "additionalProperties": False,
            },
            {"mode": "maybe"},
            "does not match",
        ),
    ],
)
async def test_pre_dispatch_argument_failures_are_recoverable(
    hass,
    parameters: dict[str, Any],
    arguments: dict[str, Any],
    expected_text: str,
) -> None:
    tool = _tool("action", parameters=parameters)
    call = _call("call-1", "action", arguments)
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)
    budget = FunctionCallBudget(3)

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [call],
        [tool],
        budget,
        None,
        [],
        recovery_state=state,
    )

    entity._execute_function_tool.assert_not_awaited()
    assert budget.used == 1
    assert state.used == 1
    result = _results(chat_log)[0].tool_result["result"]
    assert result["reason"] == "correctable_tool_error"
    assert result["stage"] == "pre_dispatch_validation"
    assert expected_text in result["error"]


async def test_disabled_recovery_preserves_previous_execution_path(hass) -> None:
    """Opting out must not insert local pre-dispatch recovery behavior."""
    tool = _tool(
        "action",
        parameters={
            "type": "object",
            "properties": {"entity_id": {"type": "string"}},
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    )
    call = _call("call-1", "action", {})
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    entity._execute_function_tool.return_value = _result(entity, call, {"status": "legacy"})

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [call],
        [tool],
        FunctionCallBudget(2),
        None,
        [],
        recovery_state=ToolRecoveryState(enabled=False),
    )

    entity._execute_function_tool.assert_awaited_once()
    assert entity._execute_function_tool.await_args.args[1].tool_args == {}
    assert _results(chat_log)[0].tool_result == {"result": {"status": "legacy"}}


async def test_normal_function_budget_applies_before_recovery(hass) -> None:
    tool = _tool(
        "action",
        parameters={
            "type": "object",
            "properties": {"required": {"type": "string"}},
            "required": ["required"],
            "additionalProperties": False,
        },
    )
    call = _call("call-1", "action")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)

    with pytest.raises(HomeAssistantError, match="Function call limit of 0 reached"):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [tool],
            FunctionCallBudget(0),
            None,
            [],
            recovery_state=state,
        )

    assert state.used == 0
    entity._execute_function_tool.assert_not_awaited()
    assert _results(chat_log)[0].tool_result["result"]["status"] == "error"


async def test_recovery_limit_fails_closed_without_dispatch(hass) -> None:
    tool = _tool(
        "action",
        parameters={
            "type": "object",
            "properties": {"required": {"type": "string"}},
            "required": ["required"],
            "additionalProperties": False,
        },
    )
    calls = [_call(f"call-{index}", "action") for index in range(1, 4)]
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, calls)
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True, limit=2)

    with pytest.raises(HomeAssistantError, match="Missing required function input"):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            calls,
            [tool],
            FunctionCallBudget(3),
            None,
            [],
            recovery_state=state,
        )

    entity._execute_function_tool.assert_not_awaited()
    assert state.used == 2
    results = _results(chat_log)
    assert [result.tool_call_id for result in results] == ["call-1", "call-2", "call-3"]
    assert [result.tool_result["result"].get("reason") for result in results[:2]] == [
        "correctable_tool_error",
        "correctable_tool_error",
    ]
    assert results[2].tool_result["result"]["status"] == "error"


async def test_post_dispatch_home_assistant_error_is_not_recovered(hass) -> None:
    tool = _tool("action")
    call = _call("call-1", "action")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    entity._execute_function_tool.side_effect = HomeAssistantError("service failed")
    state = ToolRecoveryState(enabled=True)

    with pytest.raises(HomeAssistantError, match="service failed"):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [tool],
            FunctionCallBudget(2),
            None,
            [],
            recovery_state=state,
        )

    assert state.used == 0
    entity._execute_function_tool.assert_awaited_once()
    result = _results(chat_log)[0].tool_result["result"]
    assert result["status"] == "error"
    assert result.get("reason") != "correctable_tool_error"


async def test_unknown_runtime_exception_is_not_recovered(hass) -> None:
    tool = _tool("action")
    call = _call("call-1", "action")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    entity._execute_function_tool.side_effect = RuntimeError("unknown internal failure")
    state = ToolRecoveryState(enabled=True)

    with pytest.raises(RuntimeError, match="unknown internal failure"):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [tool],
            FunctionCallBudget(2),
            None,
            [],
            recovery_state=state,
        )

    assert state.used == 0


async def test_live_tool_disappearance_remains_fail_fast(hass) -> None:
    """Availability filtering can encode security state, so it is never auto-recovery."""
    tool = _tool("action")
    call = _call("call-1", "action")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)

    with pytest.raises(FunctionNotFound):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [tool],
            FunctionCallBudget(2),
            None,
            [],
            function_tools_factory=lambda: [],
            recovery_state=state,
        )

    assert state.used == 0
    entity._execute_function_tool.assert_not_awaited()


async def test_ha_llm_tool_runtime_failure_is_not_recovered(hass) -> None:
    reference = {
        "type": "ha_llm",
        "source_type": "platform",
        "source_id": "test",
        "api_id": "assist",
        "tool_name": "do_thing",
    }
    tool = _tool("ha_saved", function=reference)
    call = _call("call-ha", "ha_saved")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    entity._execute_function_tool.side_effect = HomeAssistantError("permission denied")
    state = ToolRecoveryState(enabled=True)

    with pytest.raises(HomeAssistantError, match="permission denied"):
        await async_execute_tool_exchange(
            entity,
            chat_log,
            [call],
            [tool],
            FunctionCallBudget(2),
            None,
            [],
            recovery_state=state,
        )

    assert state.used == 0
    assert _results(chat_log)[0].tool_result["result"]["status"] == "error"


async def test_integration_owned_tool_schema_failure_can_recover_before_dispatch(hass) -> None:
    tool = _tool(
        "knowledge_search",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1}},
            "required": ["query"],
            "additionalProperties": False,
        },
        function={"type": "knowledge", "operation": "search"},
    )
    call = _call("call-k", "knowledge_search")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [call],
        [tool],
        FunctionCallBudget(2),
        None,
        [],
        recovery_state=state,
    )

    entity._execute_function_tool.assert_not_awaited()
    assert _results(chat_log)[0].tool_result["result"]["code"] == "invalid_arguments"


async def test_delayed_tool_invalid_delay_is_rejected_before_scheduling(hass) -> None:
    tool = _tool(
        "delayed_action",
        parameters={
            "type": "object",
            "properties": {
                "delay": {
                    "type": "object",
                    "properties": {"seconds": {"type": "integer", "minimum": 0}},
                    "required": ["seconds"],
                    "additionalProperties": False,
                }
            },
            "required": ["delay"],
            "additionalProperties": False,
        },
    )
    call = _call("call-delay", "delayed_action", {"delay": {"seconds": "later"}})
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [call])
    entity = _entity(hass)
    state = ToolRecoveryState(enabled=True)

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [call],
        [tool],
        FunctionCallBudget(2),
        None,
        [],
        recovery_state=state,
    )

    entity._execute_function_tool.assert_not_awaited()
    assert _results(chat_log)[0].tool_result["result"]["stage"] == "pre_dispatch_validation"


async def test_successful_serial_sibling_is_never_replayed(hass) -> None:
    first = _tool("first")
    second = _tool(
        "second",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    first_call = _call("call-1", "first")
    second_call = _call("call-2", "second")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [first_call, second_call])
    entity = _entity(hass)

    async def execute(
        _tool_definition: dict[str, Any],
        tool_input: llm.ToolInput,
        _context: Any,
        _entities: list[dict[str, Any]],
    ) -> Any:
        return _result(entity, tool_input)

    entity._execute_function_tool.side_effect = execute
    state = ToolRecoveryState(enabled=True)

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [first_call, second_call],
        [first, second],
        FunctionCallBudget(3),
        None,
        [],
        recovery_state=state,
    )

    assert entity._execute_function_tool.await_count == 1
    assert entity._execute_function_tool.await_args.args[1].id == "call-1"
    assert [result.tool_call_id for result in _results(chat_log)] == ["call-1", "call-2"]


async def test_parallel_safe_sibling_executes_once_when_other_call_is_malformed(hass) -> None:
    first = _tool("history", function={"type": "native", "name": "get_history"})
    second = _tool("statistics", function={"type": "native", "name": "get_statistics"})
    first_call = _call("call-1", "history")
    second_call = _call("call-2", "statistics")
    chat_log = _chat_log(hass)
    _retain_calls(chat_log, [first_call, second_call])
    entity = _entity(hass)
    entity._execute_function_tool.return_value = _result(entity, second_call)
    state = ToolRecoveryState(enabled=True)
    state.remember_malformed(first_call.id, "{")

    await async_execute_tool_exchange(
        entity,
        chat_log,
        [first_call, second_call],
        [first, second],
        FunctionCallBudget(2),
        None,
        [],
        recovery_state=state,
    )

    entity._execute_function_tool.assert_awaited_once()
    assert entity._execute_function_tool.await_args.args[1].id == "call-2"
    results = _results(chat_log)
    assert [result.tool_call_id for result in results] == ["call-1", "call-2"]
    assert results[0].tool_result["result"]["code"] == "invalid_json"


def _response_event(event_type: str, **kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, **kwargs)


def _response_completed() -> SimpleNamespace:
    return _response_event(
        "response.completed",
        response=SimpleNamespace(usage=SimpleNamespace(input_tokens=1, output_tokens=1)),
    )


async def test_responses_malformed_arguments_are_retained_only_when_enabled(hass) -> None:
    item = SimpleNamespace(
        type="function_call",
        call_id="bad-response",
        name="action",
        arguments="{",
    )
    stream = FakeStream(
        [
            _response_event("response.output_item.added", item=item),
            _response_event("response.output_item.done", item=item),
            _response_completed(),
        ]
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    state = ToolRecoveryState(enabled=True)

    deltas = [
        delta
        async for delta in entity._transform_responses_stream(
            _chat_log(hass), stream, recovery_state=state
        )
    ]
    tool_input = next(delta["tool_calls"][0] for delta in deltas if "tool_calls" in delta)
    assert isinstance(tool_input.tool_args, MalformedToolArguments)
    assert state.pop_malformed(tool_input.id) is not None

    disabled_stream = FakeStream(
        [
            _response_event("response.output_item.added", item=item),
            _response_event("response.output_item.done", item=item),
        ]
    )
    with pytest.raises(ParseArgumentsFailed):
        _ = [
            delta
            async for delta in entity._transform_responses_stream(
                _chat_log(hass), disabled_stream, recovery_state=ToolRecoveryState(False)
            )
        ]


async def test_chat_completions_malformed_arguments_are_retained_when_enabled(hass) -> None:
    delta = SimpleNamespace(
        content=None,
        refusal=None,
        tool_calls=[
            SimpleNamespace(
                index=0,
                id="bad-chat",
                function=SimpleNamespace(name="action", arguments="{"),
            )
        ],
    )
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason="tool_calls")],
        usage=None,
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    state = ToolRecoveryState(enabled=True)

    deltas = [
        item
        async for item in entity._transform_chat_stream(
            _chat_log(hass), FakeStream([chunk]), recovery_state=state
        )
    ]
    tool_input = next(item["tool_calls"][0] for item in deltas if "tool_calls" in item)
    assert isinstance(tool_input.tool_args, MalformedToolArguments)
    assert state.pop_malformed(tool_input.id) is not None


@pytest.mark.parametrize("converter", [_convert_content_to_param, _convert_content_to_responses_param])
def test_malformed_arguments_replay_raw_provider_text_in_both_protocols(hass, converter) -> None:
    call = llm.ToolInput(
        id="bad-1",
        tool_name="action",
        tool_args=MalformedToolArguments("{"),
        external=True,
    )
    content = [conversation.AssistantContent(agent_id="conversation.recovery", tool_calls=[call])]

    serialized = converter(content)
    if converter is _convert_content_to_param:
        assert serialized[0]["tool_calls"][0]["function"]["arguments"] == "{"
    else:
        assert serialized[0]["arguments"] == "{"


def test_recovery_error_text_is_bounded(hass) -> None:
    call = _call("call-1", "action")
    failure = correctable_validation_failure(HomeAssistantError("x" * 2000))
    result = recovery_tool_result("conversation.recovery", call, failure)
    error = result.tool_result["result"]["error"]
    assert len(error) <= 320
    assert "Traceback" not in error
