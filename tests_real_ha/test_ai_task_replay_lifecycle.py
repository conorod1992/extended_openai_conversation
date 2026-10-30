"""Loaded AI Task provider exchanges settle replay history at acknowledgement."""

import asyncio

from openai import OpenAIError
import pytest
import voluptuous as vol

from custom_components.extended_openai_conversation_responses import (
    execution_outcomes as outcomes,
    tool_replay_guard as guard,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    replay_safety,
)
from homeassistant.components import ai_task
from homeassistant.components.ai_task.const import DATA_COMPONENT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from tests_real_ha.chat_sessions import async_get_chat_session
from tests_real_ha.test_ai_task_runtime import (
    CallerAPI,
    _chunk,
    _setup_entry,
    _tool_delta,
)


class _IncrementCounter(llm.Tool):
    name = "increment_counter"
    integration = "extended_openai_conversation_responses"
    description = "Increment the acceptance-test counter once."
    parameters = vol.Schema({})

    def __init__(self):
        self.calls = []

    async def async_call(self, hass, tool_input, llm_context):
        self.calls.append((tool_input, llm_context))
        await hass.services.async_call(
            "counter",
            "increment",
            {"entity_id": "counter.receipt_probe"},
            blocking=True,
            context=llm_context.context,
        )
        payload = {"count": hass.states.get("counter.receipt_probe").state}
        result_type = getattr(llm, "ToolResult", None)
        return result_type(data=payload) if result_type is not None else payload


def _request_tool(call_id):
    def stream(kwargs):
        alias = kwargs["tools"][0]["function"]["name"]
        return [
            _chunk(
                finish_reason="tool_calls",
                tool_calls=[_tool_delta(0, alias, "{}", call_id)],
            )
        ]

    return stream


async def _loaded_task(hass, provider_outcomes):
    # Recorder must install its registry listener before counter entities do.
    _, entity_id, client = await _setup_entry(hass, provider_outcomes)
    assert await async_setup_component(
        hass, "counter", {"counter": {"receipt_probe": {"initial": 0}}}
    )
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    assert entity is not None
    tool = _IncrementCounter()
    caller_api = CallerAPI(hass=hass, id="receipt-probe", name="Receipt Probe")
    caller_api.tools = [tool]
    return entity, entity_id, client, tool, caller_api


async def _generate_in_session(
    hass, entity, caller_api, conversation_id, structure=None
):
    async def generate():
        with async_get_chat_session(hass, conversation_id) as session:
            return await entity.internal_async_generate_data(
                session,
                ai_task.GenDataTask(
                    name="Receipt lifecycle",
                    instructions="Use the caller API.",
                    llm_api=caller_api,
                    structure=structure,
                ),
            )

    # Match Core's request task boundary, including after a failed ChatLog context.
    return await asyncio.create_task(generate())


def _assert_no_retained_records(entity):
    for name in (
        "_dispatched_tool_inputs",
        "_dispatched_tool_signatures",
        "_dispatched_tool_conversations",
        "_read_only_tool_inputs",
        "_execution_tool_outcomes",
        "_execution_effect_receipts",
        "retired_conversations",
        "schemas",
        "effects",
        "uncertain_tools",
    ):
        assert not getattr(entity, name, {}), name
    assert not replay_safety(entity).history_incomplete


@pytest.mark.parametrize("reused_conversation", [False, True])
async def test_loaded_successful_tasks_release_replay_history(
    hass, monkeypatch, reused_conversation
):
    provider_outcomes = [
        outcome
        for index in range(8)
        for outcome in (_request_tool(f"call-{index}"), f"accepted-{index}")
    ]
    entity, entity_id, _, tool, caller_api = await _loaded_task(hass, provider_outcomes)
    monkeypatch.setattr(guard, "_MAX_DISPATCH_RECORDS", 2)
    monkeypatch.setattr(guard, "_MAX_RETIRED_CONVERSATIONS", 2)
    monkeypatch.setattr(outcomes, "_MAX_RETAINED_EFFECT_RECEIPTS", 2)
    for index in range(8):
        if reused_conversation:
            result = await _generate_in_session(
                hass, entity, caller_api, "reused-receipt-task"
            )
        else:
            result = await ai_task.async_generate_data(
                hass,
                entity_id=entity_id,
                task_name="Fresh receipt task",
                instructions="Use the caller API.",
                llm_api=caller_api,
            )
        assert result.data == f"accepted-{index}"
        _assert_no_retained_records(entity)
    assert hass.states.get("counter.receipt_probe").state == "8"
    assert len(tool.calls) == 8
    assert caller_api.tools == [tool]


@pytest.mark.parametrize("failure", ["provider_error", "cancel"])
async def test_loaded_failed_task_preserves_effect_until_provider_ack(
    hass, monkeypatch, failure
):
    entity, _, client, tool, caller_api = await _loaded_task(
        hass, [_request_tool("initial-effect"), OpenAIError("lost continuation")]
    )
    conversation_id = "failed-receipt-task"
    if failure == "cancel":
        started = asyncio.Event()
        original_create = client.completions.create
        request_count = 0

        async def create(**kwargs):
            nonlocal request_count
            request_count += 1
            if request_count == 2:
                started.set()
                await asyncio.Event().wait()
            return await original_create(**kwargs)

        monkeypatch.setattr(client.completions, "create", create)
        task = asyncio.create_task(
            _generate_in_session(hass, entity, caller_api, conversation_id)
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(HomeAssistantError, match="Provider request failed"):
            await _generate_in_session(hass, entity, caller_api, conversation_id)
    assert hass.states.get("counter.receipt_probe").state == "1"
    assert len(tool.calls) == 1
    assert replay_safety(entity).effects[conversation_id]
    assert set(entity._dispatched_tool_conversations.values()) == {conversation_id}

    client.completions.outcomes[:] = [
        _request_tool("retry-effect"),
        "acknowledged without tools",
        _request_tool("permitted-after-ack"),
        "accepted after acknowledgement",
    ]
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await _generate_in_session(hass, entity, caller_api, conversation_id)
    assert hass.states.get("counter.receipt_probe").state == "1"
    assert len(tool.calls) == 1
    acknowledged = await _generate_in_session(hass, entity, caller_api, conversation_id)
    assert acknowledged.data == "acknowledged without tools"
    _assert_no_retained_records(entity)
    result = await _generate_in_session(hass, entity, caller_api, conversation_id)
    assert result.data == "accepted after acknowledgement"
    assert hass.states.get("counter.receipt_probe").state == "2"
    assert len(tool.calls) == 2
    _assert_no_retained_records(entity)


async def test_loaded_structured_result_failure_keeps_provider_acknowledgement(hass):
    entity, _, _, tool, caller_api = await _loaded_task(
        hass,
        [
            _request_tool("accepted-invalid-result"),
            '{"answer":"invalid"}',
            _request_tool("retry-after-output-error"),
            '{"answer":2}',
        ],
    )
    from homeassistant.components.ai_task import task as task_module

    engine = getattr(task_module, "probatio", vol)
    structure = engine.Schema({engine.Required("answer"): int})
    with pytest.raises(HomeAssistantError, match="does not match"):
        await _generate_in_session(
            hass, entity, caller_api, "structured-receipt-task", structure
        )
    assert hass.states.get("counter.receipt_probe").state == "1"
    _assert_no_retained_records(entity)
    result = await _generate_in_session(
        hass, entity, caller_api, "structured-receipt-task", structure
    )
    assert result.data == {"answer": 2}
    assert hass.states.get("counter.receipt_probe").state == "2"
    assert len(tool.calls) == 2
    _assert_no_retained_records(entity)
