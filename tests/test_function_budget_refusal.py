"""Expected budget refusal preserves protocol, execution accounting and replay safety."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_param,
    _convert_content_to_responses_param,
)
from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    FUNCTION_TOOL_LIMIT_REASON,
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    async_was_unacknowledged_equivalent,
    record_dispatch,
    remember_unacknowledged_calls,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _results,
    _retain_calls,
    _tool,
)


@pytest.mark.parametrize("limit", [0, 1, 3, 4, -1])
@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
async def test_budget_prefix_pairing_and_public_wire(hass, limit, parallel, recovery):
    tools = [
        _tool(f"tool_{n}", function={"type": "memory", "operation": "search"})
        if parallel
        else _tool(f"tool_{n}")
        for n in range(3)
    ]
    calls = [_call(f"call_{n}", tool["spec"]["name"]) for n, tool in enumerate(tools)]
    log, entity = _chat_log(hass), _entity(hass)
    _retain_calls(log, calls)
    executed = []

    async def execute(_tool, call, *_args):
        executed.append(call.id)
        return _result(entity, call)

    entity._execute_function_tool.side_effect = execute
    budget, state = FunctionCallBudget(limit), ToolRecoveryState(enabled=recovery)
    await async_execute_tool_exchange(
        entity, log, calls, tools, budget, None, [], recovery_state=state
    )
    count = 3 if limit < 0 else min(limit, 3)
    assert executed == [call.id for call in calls[:count]]
    assert budget.used == count and state.used == 0
    results = _results(log)
    assert [r.tool_call_id for r in results] == [c.id for c in calls]
    for result in results[count:]:
        assert tool_result_data(result) == {
            "result": {"status": "not_executed", "reason": FUNCTION_TOOL_LIMIT_REASON}
        }
        for convert, field in [
            (_convert_content_to_param, "content"),
            (_convert_content_to_responses_param, "output"),
        ]:
            wire = convert([result])[0]
            assert (
                json.loads(wire[field])["result"]["reason"]
                == "not executed: request Function Tool limit reached"
            )
    assert not await async_was_unacknowledged_equivalent(entity, log, calls[-1])


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
async def test_validation_failure_never_reserves_execution(hass, parallel, recovery):
    params = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    tool = _tool(
        "tool",
        parameters=params,
        function={"type": "memory", "operation": "search"} if parallel else None,
    )
    calls = [
        _call("invalid", "tool"),
        _call("allowed", "tool", {"value": "ok"}),
        _call("overflow", "tool", {"value": "ok"}),
    ]
    log, entity = _chat_log(hass), _entity(hass)
    _retain_calls(log, calls)
    entity._execute_function_tool.side_effect = lambda _tool, call, *_args: _result(
        entity, call
    )
    budget, state = FunctionCallBudget(1), ToolRecoveryState(enabled=recovery)
    if recovery:
        await async_execute_tool_exchange(
            entity, log, calls, [tool], budget, None, [], recovery_state=state
        )
        assert budget.used == 1 and state.used == 1
        assert entity._execute_function_tool.await_args.args[1].id == "allowed"
        assert (
            tool_result_data(_results(log)[-1])["result"]["reason"]
            == FUNCTION_TOOL_LIMIT_REASON
        )
    else:
        with pytest.raises(HomeAssistantError, match="Missing required"):
            await async_execute_tool_exchange(
                entity, log, calls, [tool], budget, None, [], recovery_state=state
            )
        assert budget.used == state.used == 0
        entity._execute_function_tool.assert_not_awaited()
    assert len(_results(log)) == len(calls)


@pytest.mark.parametrize("recovery", [False, True])
async def test_refusal_cannot_clear_existing_unacknowledged_effect(hass, recovery):
    log, entity = _chat_log(hass), _entity(hass)
    effect = _call("effect", "old_tool")
    _retain_calls(log, [effect])
    record_dispatch(entity, effect)
    log.async_add_assistant_content_without_tools(_result(entity, effect))
    remember_unacknowledged_calls(entity, log, set())
    refused = _call("refused", "new_tool")
    _retain_calls(log, [refused])
    await async_execute_tool_exchange(
        entity,
        log,
        [refused],
        [_tool("new_tool")],
        FunctionCallBudget(0),
        None,
        [],
        recovery_state=ToolRecoveryState(enabled=recovery),
    )
    entity._execute_function_tool.assert_not_awaited()
    assert await async_was_unacknowledged_equivalent(
        entity, log, _call("new_id", "old_tool")
    )
    assert not await async_was_unacknowledged_equivalent(
        entity, log, _call("retry", "new_tool")
    )


def test_atomic_prefix_reservation():
    budget = FunctionCallBudget(3, used=1)
    assert budget.claim_prefix(["a", "b", "c"]) == 2
    assert budget.used == 3
    assert budget.claim_prefix(["d"]) == 0


async def test_parallel_cancellation_preserves_decided_overflow_result(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import tool_exchange

    calls = [_call("accepted", "tool"), _call("overflow", "tool")]
    tool = _tool("tool", function={"type": "memory", "operation": "search"})
    log, entity = _chat_log(hass), _entity(hass)
    _retain_calls(log, calls)
    monkeypatch.setattr(tool_exchange, "async_execute_parallel_safe_batch_outcomes", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await async_execute_tool_exchange(entity, log, calls, [tool], FunctionCallBudget(1), None, [])
    results = {result.tool_call_id: result for result in _results(log)}
    assert len(_results(log)) == 2
    assert tool_result_data(results["overflow"])["result"]["reason"] == FUNCTION_TOOL_LIMIT_REASON
