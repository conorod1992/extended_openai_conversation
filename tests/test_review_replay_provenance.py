"""Validation copies must preserve trusted execution provenance."""

import pytest

from custom_components.extended_openai_conversation_responses.function_call_budget import FunctionCallBudget
from custom_components.extended_openai_conversation_responses.function_tool_recovery import ToolRecoveryState
from custom_components.extended_openai_conversation_responses.tool_exchange import async_execute_tool_exchange
from custom_components.extended_openai_conversation_responses.tool_replay_guard import record_dispatch, remember_unacknowledged_calls, was_unacknowledged_equivalent
from tests.test_function_tool_error_recovery import _call, _chat_log, _entity, _retain_calls, _result, _tool


@pytest.mark.parametrize("parallel", [False, True])
async def test_recovery_records_retained_calls_after_real_dispatch(hass, parallel):
    entity = _entity(hass)
    chat_log = _chat_log(hass)
    calls = [_call("first", "effect", {"amount": 1})]
    tools = [_tool("effect", parameters={"type": "object", "properties": {"amount": {"type": "integer"}}, "required": ["amount"]})]
    if parallel:
        tools[0]["function"] = {"type": "memory", "operation": "list"}
        calls.append(_call("second", "effect", {"amount": 2}))
    effects = []

    async def execute(tool, tool_input, *_):
        record_dispatch(entity, tool_input)
        effects.append(tool_input)
        return _result(entity, tool_input)

    entity._execute_function_tool.side_effect = execute
    before = {id(content) for content in chat_log.content}
    _retain_calls(chat_log, calls)
    await async_execute_tool_exchange(entity, chat_log, calls, tools, FunctionCallBudget(10), None, [], recovery_state=ToolRecoveryState(enabled=True))
    assert all(dispatched is not retained for dispatched, retained in zip(effects, calls, strict=True))
    remember_unacknowledged_calls(entity, chat_log, before)
    for call in calls:
        retry = _call("fresh-id", call.tool_name, call.tool_args)
        assert was_unacknowledged_equivalent(entity, chat_log, retry)
    retry = _call("retry", "effect", {"amount": 1})
    _retain_calls(chat_log, [retry])
    from homeassistant.exceptions import HomeAssistantError
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(entity, chat_log, [retry], tools, FunctionCallBudget(10), None, [], recovery_state=ToolRecoveryState(enabled=True))
    assert len(effects) == len(calls)


async def test_correctable_validation_failure_does_not_record_dispatch(hass):
    entity = _entity(hass)
    chat_log = _chat_log(hass)
    call = _call("invalid", "effect")
    tool = _tool("effect", parameters={"type": "object", "properties": {"amount": {"type": "integer"}}, "required": ["amount"]})
    before = {id(content) for content in chat_log.content}
    _retain_calls(chat_log, [call])
    await async_execute_tool_exchange(entity, chat_log, [call], [tool], FunctionCallBudget(10), None, [], recovery_state=ToolRecoveryState(enabled=True))
    remember_unacknowledged_calls(entity, chat_log, before)
    assert not was_unacknowledged_equivalent(entity, chat_log, _call("fresh", "effect"))
    entity._execute_function_tool.assert_not_awaited()

