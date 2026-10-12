"""Replay-equivalence contracts at the real tool exchange, not only signatures.

Every rejected replay has a completed observable effect from the first call.
Non-equivalent cases must still dispatch; a deny-all replay guard is incorrect.
"""

from copy import deepcopy

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.function_call_budget import (
    FunctionCallBudget,
)
from custom_components.extended_openai_conversation_responses.function_tool_recovery import (
    ToolRecoveryState,
)
from custom_components.extended_openai_conversation_responses.tool_exchange import (
    async_execute_tool_exchange,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    record_dispatch,
    remember_unacknowledged_calls,
)
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)
from tests.functions.behaviour_generators import assert_typed_value


# Only pairs permitted by the declared schema should canonicalize as equivalent.
@pytest.mark.parametrize(
    "schema,initial,repeated,equivalent",
    [
        (
            {"payload": {"type": "array", "items": {"type": "number"}}},
            {"payload": [1, 2]},
            {"payload": [1.0, 2.0]},
            True,
        ),
        (
            {"payload": {"type": "object", "properties": {
                "value": {"type": ["integer", "null"]},
            }, "required": ["value"]}},
            {"payload": {"value": 1}},
            {"payload": {"value": 1.0}},
            True,
        ),
        (
            {"payload": {"type": "object", "properties": {
                "value": {"type": ["integer", "null"]},
            }, "required": ["value"]}},
            {"payload": {"value": None}},
            {"payload": {"value": 0}},
            False,
        ),
        (
            {"payload": {"type": ["integer", "boolean"]}},
            {"payload": 0},
            {"payload": False},
            False,
        ),
        (
            {"payload": {"type": "string"}},
            {"payload": "0012"},
            {"payload": "12"},
            False,
        ),
    ],
    ids=[
        "nested-integral-numbers",
        "nullable-nested-integral-number",
        "null-versus-zero",
        "boolean-versus-number",
        "leading-zero-string",
    ],
)
async def test_replay_guard_differentiates_canonical_and_distinct_inputs(
    hass, schema, initial, repeated, equivalent
):
    entity, log = _entity(hass), _chat_log(hass)
    tool = _tool(
        "effect",
        parameters={"type": "object", "properties": schema, "required": ["payload"]},
    )
    effects = []

    async def dispatch(definition, validated, *_args):
        # This is the integration's normal exchange, with only the business
        # implementation replaced by an observable side-effect witness.
        record_dispatch(entity, validated, definition)
        effects.append(deepcopy(validated.tool_args))
        return _result(entity, validated, {"status": "skipped"})

    entity._execute_function_tool.side_effect = dispatch
    before = {id(item) for item in log.content}
    first = _call("first-call", "effect", initial)
    _retain_calls(log, [first])
    state = ToolRecoveryState(enabled=True)
    await async_execute_tool_exchange(
        entity, log, [first], [tool], FunctionCallBudget(5), None, [],
        recovery_state=state,
    )
    assert len(effects) == 1
    remember_unacknowledged_calls(entity, log, before)

    retry = _call("new-provider-call-id", "effect", repeated)
    _retain_calls(log, [retry])
    if equivalent:
        with pytest.raises(HomeAssistantError, match="not acknowledged"):
            await async_execute_tool_exchange(
                entity, log, [retry], [tool], FunctionCallBudget(5), None, [],
                recovery_state=state,
            )
        assert len(effects) == 1
    else:
        await async_execute_tool_exchange(
            entity, log, [retry], [tool], FunctionCallBudget(5), None, [],
            recovery_state=state,
        )
        assert len(effects) == 2
        assert_typed_value(effects[-1], repeated)


@pytest.mark.parametrize(
    "leaf,raw,expected",
    [
        ({"type": "string"}, "0012", "0012"),
        ({"type": "boolean"}, False, False),
        ({"type": ["integer", "null"]}, None, None),
        ({"type": "array", "items": {"type": ["integer", "null"]}},
         [0, None, 2], [0, None, 2]),
        ({"type": "object", "properties": {
            "flag": {"type": "boolean"}, "code": {"type": "string"},
        }, "required": ["flag", "code"]},
         {"flag": False, "code": "000"}, {"flag": False, "code": "000"}),
    ],
    ids=["numeric-looking-string", "false-is-not-zero", "null-is-not-zero",
         "nested-nullable-array", "nested-distinct-scalars"],
)
async def test_validated_arguments_reach_actual_exchange_dispatch_with_types(
    hass, leaf, raw, expected
):
    """Verify observable dispatch arguments, not only validator return values."""
    entity, log = _entity(hass), _chat_log(hass)
    tool = _tool("effect", parameters={
        "type": "object", "properties": {"payload": leaf},
        "required": ["payload"], "additionalProperties": False,
    })
    seen = []

    async def dispatch(definition, validated, *_args):
        record_dispatch(entity, validated, definition)
        seen.append(deepcopy(validated.tool_args))
        return _result(entity, validated)

    entity._execute_function_tool.side_effect = dispatch
    call = _call("typed", "effect", {"payload": raw})
    _retain_calls(log, [call])
    await async_execute_tool_exchange(
        entity, log, [call], [tool], FunctionCallBudget(2), None, [],
        recovery_state=ToolRecoveryState(enabled=True),
    )
    assert len(seen) == 1
    assert_typed_value(seen[0], {"payload": expected})
