"""Regressions for lost continuations and cross-API retained history."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.debug import DebugTrace
from custom_components.extended_openai_conversation_responses.entity import (
    _convert_content_to_param,
)
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
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_tool_error_recovery import (
    FakeStream,
    _call,
    _chat_log,
    _entity,
    _result,
    _retain_calls,
    _tool,
)
from tests.test_provider_stream_terminals import _entity as _stream_entity


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize(
    "initial,retry,schema",
    [
        ({"amount": "1"}, {"amount": 1}, {"amount": {"type": "integer"}}),
        (
            {"target": "counter.test"},
            {"target": ["counter.test"]},
            {"target": {"type": "array", "items": {"type": "string"}}},
        ),
    ],
)
async def test_canonical_retry_cannot_repeat_skipped_business_result(
    hass, recovery, initial, retry, schema
):
    entity, log = _entity(hass), _chat_log(hass)
    tool = _tool("effect", parameters={"type": "object", "properties": schema})
    effects = []

    async def execute(config, call, *_):
        record_dispatch(entity, call, config)
        effects.append(call)
        return _result(entity, call, {"status": "skipped"})

    entity._execute_function_tool.side_effect = execute
    existing = {id(item) for item in log.content}
    call = _call("first", "effect", initial)
    _retain_calls(log, [call])
    state = ToolRecoveryState(enabled=recovery)
    await async_execute_tool_exchange(
        entity,
        log,
        [call],
        [tool],
        FunctionCallBudget(10),
        None,
        [],
        recovery_state=state,
    )
    remember_unacknowledged_calls(entity, log, existing)
    repeated = _call("fresh", "effect", retry)
    _retain_calls(log, [repeated])
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await async_execute_tool_exchange(
            entity,
            log,
            [repeated],
            [tool],
            FunctionCallBudget(10),
            None,
            [],
            recovery_state=state,
        )
    assert len(effects) == 1


def test_parallel_history_groups_calls_without_crossing_results():
    a, b, c = (_call(name, "effect") for name in ("a", "b", "c"))
    entity = SimpleNamespace(entity_id="agent")
    content = [
        conversation.AssistantContent(agent_id="agent", tool_calls=[a]),
        conversation.AssistantContent(agent_id="agent", tool_calls=[b]),
        _result(entity, a),
        _result(entity, b),
        conversation.AssistantContent(agent_id="agent", tool_calls=[c]),
        _result(entity, c),
    ]
    messages = _convert_content_to_param(content)
    assert [message["role"] for message in messages] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
        "tool",
    ]
    assert [call["id"] for call in messages[0]["tool_calls"]] == ["a", "b"]


async def test_failed_response_removes_only_its_reasoning_and_keeps_calls():
    previous = conversation.AssistantContent(
        agent_id="agent", native=SimpleNamespace(type="reasoning", id="old")
    )
    log = SimpleNamespace(content=[previous])
    stream = FakeStream(
        [
            SimpleNamespace(
                type="response.output_item.done",
                output_index=0,
                item=SimpleNamespace(type="reasoning", id="new"),
            ),
            SimpleNamespace(
                type="response.output_item.done",
                output_index=1,
                item=SimpleNamespace(
                    type="function_call",
                    id="fc",
                    call_id="call",
                    name="effect",
                    arguments="{}",
                ),
            ),
        ]
    )
    with pytest.raises(HomeAssistantError, match="terminal"):
        async for delta in _stream_entity()._transform_responses_stream(log, stream):
            log.content.append(conversation.AssistantContent(agent_id="agent", **delta))
    assert log.content[0] is previous
    assert len(log.content) == 2
    assert log.content[1].tool_calls[0].id == "call"


def test_debug_summary_uses_first_request_with_text():
    trace = DebugTrace("debug", "entry", "agent", "now", {}, None)
    trace.start_provider_request("embeddings.create", (), {})
    trace.start_provider_request("responses.create", (), {})  # tool-only prefix
    request = trace.start_provider_request("responses.create", (), {})
    request.first_text_ms = 123
    assert trace.summary()["first_text_ms"] == 123
