"""Native output is replayed whole, or downgraded as one interrupted response."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai.types.responses import (
    Response,
    ResponseReasoningItem,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseCompletedEvent,
    ResponseTextDeltaEvent,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
    CONTINUE_CONVERSATION_TOOL_NAME,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)
from tests.test_responses_api import FakeStream


def reasoning(name):
    return ResponseReasoningItem(
        id=name, summary=[], encrypted_content="opaque-" + name, type="reasoning"
    )


def call(name, call_id, arguments=None):
    return ResponseFunctionToolCall(
        id="fc_" + call_id,
        call_id=call_id,
        name=name,
        arguments=json.dumps(arguments or {}),
        type="function_call",
        status="completed",
    )


def message(name, text):
    return ResponseOutputMessage(
        id=name,
        role="assistant",
        type="message",
        status="completed",
        phase="commentary",
        content=[{"type": "output_text", "text": text, "annotations": []}],
    )


def events(items, *, complete=True, unfinished=False):
    result = []
    for index, item in enumerate(items):
        result.append(
            ResponseOutputItemAddedEvent(
                type="response.output_item.added",
                item=item,
                output_index=index,
                sequence_number=len(result),
            )
        )
        if isinstance(item, ResponseOutputMessage):
            result.append(
                ResponseTextDeltaEvent(
                    type="response.output_text.delta",
                    delta=item.content[0].text,
                    output_index=index,
                    content_index=0,
                    item_id=item.id,
                    logprobs=[],
                    sequence_number=len(result),
                )
            )
        result.append(
            ResponseOutputItemDoneEvent(
                type="response.output_item.done",
                item=item,
                output_index=index,
                sequence_number=len(result),
            )
        )
    if unfinished:
        pending = call("effect", "unfinished")
        result.append(
            ResponseOutputItemAddedEvent(
                type="response.output_item.added",
                item=pending.model_copy(
                    update={"status": "in_progress", "arguments": ""}
                ),
                output_index=len(items),
                sequence_number=len(result),
            )
        )
    if complete:
        result.append(
            ResponseCompletedEvent(
                type="response.completed",
                response=Response.model_construct(
                    id="response", usage=None, output=items
                ),
                sequence_number=len(result),
            )
        )
    return FakeStream(result)


def entity(hass, streams):
    obj = object.__new__(ExtendedOpenAIBaseLLMEntity)
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=streams))
    )
    obj.entry = SimpleNamespace(runtime_data=client, data={})
    obj.subentry = SimpleNamespace(
        data={"chat_model": "gpt-5.6-luna", "api_mode": "responses"}
    )
    obj.entity_id = "conversation.coherent"
    obj.hass = hass
    return obj, client


async def exercise_reasoning_finalizer_history(hass, commentary):
    native = [reasoning("rs_one"), reasoning("rs_two")]
    if commentary:
        native.append(message("msg_before_finalizer", "Considering the room."))
    final = call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        "final",
        {"response": "Which room?", "continue_conversation": True},
    )
    native.append(final)
    second = call(
        CONTINUE_CONVERSATION_TOOL_NAME,
        "second",
        {"response": "Kitchen selected", "continue_conversation": False},
    )
    obj, client = entity(hass, [events(native), events([second])])
    log = conversation.ChatLog(hass, "coherent")
    log.async_add_user_content(conversation.UserContent(content="Help me"))
    assert (
        await obj._async_handle_chat_log(log, [], [], conditional_continue=True) is True
    )
    assert log.content[-1].content == "Which room?"
    log.async_add_user_content(conversation.UserContent(content="The kitchen"))
    assert (
        await obj._async_handle_chat_log(log, [], [], conditional_continue=True)
        is False
    )
    outgoing = client.responses.create.await_args_list[1].kwargs["input"]
    original_ids = [item.id for item in native]
    assert [
        item.get("id") for item in outgoing if item.get("id") in original_ids
    ] == original_ids
    calls = [
        item
        for item in outgoing
        if item.get("type") == "function_call" and item.get("call_id") == "final"
    ]
    assert len(calls) == 1
    assert calls[0] == final.model_dump(exclude_none=True)
    outputs = [
        item
        for item in outgoing
        if item.get("type") == "function_call_output" and item.get("call_id") == "final"
    ]
    assert len(outputs) == 1


async def exercise_interrupted_response_history(
    hass,
):
    completed = [reasoning("rs_completed"), call("effect", "completed")]
    failed = [
        reasoning("rs_interrupted"),
        message("msg_commentary", "I will check the light."),
    ]
    obj, client = entity(
        hass,
        [
            events(completed),
            events(failed, complete=False, unfinished=True),
            events([message("msg_answer", "Recovered")]),
        ],
    )
    obj._execute_function_tool = AsyncMock(
        return_value=make_tool_result_content(
            agent_id=obj.entity_id,
            tool_call_id="completed",
            tool_name="effect",
            tool_result={"result": "effect completed once"},
        )
    )
    tool = {
        "spec": {
            "name": "effect",
            "description": "Effect",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "unused"},
    }
    log = conversation.ChatLog(hass, "interrupted")
    log.async_add_user_content(conversation.UserContent(content="Run effect"))
    from custom_components.extended_openai_conversation_responses.completion_events import (
        completion_event_scope,
    )

    async with completion_event_scope():
        with pytest.raises(HomeAssistantError, match="terminal"):
            await obj._async_handle_chat_log(log, [tool], [])
    log.async_add_user_content(conversation.UserContent(content="Continue"))
    await obj._async_handle_chat_log(log, [tool], [])
    outgoing = client.responses.create.await_args_list[2].kwargs["input"]
    assert not any(
        item.get("id") in {"rs_interrupted", "msg_commentary"} for item in outgoing
    )
    assert any(item.get("id") == "rs_completed" for item in outgoing)
    assert any(item.get("id") == "fc_completed" for item in outgoing)
    assert any(
        item.get("type") == "message"
        and item.get("content") == "I will check the light."
        and "id" not in item
        for item in outgoing
    )
    assert (
        len(
            [
                item
                for item in outgoing
                if item.get("call_id") == "completed"
                and item.get("type") == "function_call_output"
            ]
        )
        == 1
    )
    obj._execute_function_tool.assert_awaited_once()


@pytest.mark.parametrize("commentary", [False, True])
async def test_reasoning_finalizer_replayed_with_original_call_and_result(
    hass, commentary
):
    await exercise_reasoning_finalizer_history(hass, commentary)


async def test_interrupted_response_drops_native_ids_without_losing_completed_effect(
    hass,
):
    await exercise_interrupted_response_history(hass)


@pytest.mark.parametrize("draft", [False, True])
async def test_conditional_retry_preserves_complete_earlier_native_output(hass, draft):
    first = [reasoning("rs_first")]
    if draft:
        first.append(message("msg_draft", "Superseded draft"))
    else:
        first.append(
            call(
                CONTINUE_CONVERSATION_TOOL_NAME,
                "premature",
                {"response": "Too early", "continue_conversation": False},
            )
        )
        first.append(call("effect", "business"))
    final = [
        reasoning("rs_final"),
        call(
            CONTINUE_CONVERSATION_TOOL_NAME,
            "final",
            {"response": "Correct answer", "continue_conversation": True},
        ),
    ]
    next_turn = [
        call(
            CONTINUE_CONVERSATION_TOOL_NAME,
            "next",
            {"response": "Next answer", "continue_conversation": False},
        )
    ]
    obj, client = entity(hass, [events(first), events(final), events(next_turn)])
    obj._execute_function_tool = AsyncMock(
        return_value=make_tool_result_content(
            agent_id=obj.entity_id,
            tool_call_id="business",
            tool_name="effect",
            tool_result={"result": "done"},
        )
    )
    tool = {
        "spec": {
            "name": "effect",
            "description": "Effect",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "unused"},
    }
    log = conversation.ChatLog(hass, "retry")
    heard = []
    log.delta_listener = lambda _log, delta: heard.append(delta)
    log.async_add_user_content(conversation.UserContent(content="Start"))
    assert (
        await obj._async_handle_chat_log(log, [tool], [], conditional_continue=True)
        is True
    )
    assert log.content[-1].content == "Correct answer"
    assert not any(
        "Superseded draft" in delta.get("content", "")
        or "Too early" in delta.get("content", "")
        for delta in heard
    )
    log.async_add_user_content(conversation.UserContent(content="Next"))
    await obj._async_handle_chat_log(log, [tool], [], conditional_continue=True)
    outgoing = client.responses.create.await_args_list[2].kwargs["input"]
    original = first + final
    assert [
        item.get("id")
        for item in outgoing
        if item.get("id") in {o.id for o in original}
    ] == [o.id for o in original]
    if draft:
        assert not any(
            getattr(item, "content", None) == "Superseded draft" for item in log.content
        )
        obj._execute_function_tool.assert_not_awaited()
    else:
        assert any(
            item.get("type") == "function_call_output"
            and item.get("call_id") == "premature"
            for item in outgoing
        )
        obj._execute_function_tool.assert_awaited_once()
