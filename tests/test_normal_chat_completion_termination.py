"""Successful normal Chat completion is terminal even without spoken text."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

from openai.types.chat import ChatCompletion, ChatCompletionChunk
import pytest

from custom_components.extended_openai_conversation_responses import (
    entity as entity_module,
)
from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    is_tool_result_content,
    make_tool_result_content,
    tool_result_data,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_responses_api import FakeStream, _control_entity


def response(streaming, *, answer=None, refusal=None, action=False, finish="stop"):
    tools = (
        [
            {
                "id": "action",
                "type": "function",
                "function": {"name": "effect", "arguments": "{}"},
            }
        ]
        if action
        else None
    )
    if streaming:
        delta = {"role": "assistant", "content": answer, "refusal": refusal}
        if tools:
            delta["tool_calls"] = [{"index": i, **tool} for i, tool in enumerate(tools)]
        choices = [{"index": 0, "delta": delta, "finish_reason": None}]
        chunks = [
            ChatCompletionChunk(
                id="chat",
                object="chat.completion.chunk",
                created=0,
                model="gpt-4o-mini",
                choices=choices,
            )
        ]
        if finish is not None:
            chunks.append(
                ChatCompletionChunk(
                    id="chat",
                    object="chat.completion.chunk",
                    created=0,
                    model="gpt-4o-mini",
                    choices=[{"index": 0, "delta": {}, "finish_reason": finish}],
                )
            )
            chunks.append(
                ChatCompletionChunk(
                    id="chat",
                    object="chat.completion.chunk",
                    created=0,
                    model="gpt-4o-mini",
                    choices=[],
                    usage={
                        "prompt_tokens": 8,
                        "completion_tokens": 2,
                        "total_tokens": 10,
                    },
                )
            )
        return FakeStream(chunks)
    return ChatCompletion(
        id="chat",
        object="chat.completion",
        created=0,
        model="gpt-4o-mini",
        choices=[
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                    "refusal": refusal,
                    "tool_calls": tools,
                },
                "finish_reason": finish,
            }
        ],
        usage={"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    )


async def exercise_normal_chat_completion(
    hass,
    monkeypatch,
    streaming,
    answer,
    *,
    action_first=True,
    finish="stop",
    refusal=None,
):
    entity, log, create = _control_entity(hass, "chat_completions", [])
    entity.subentry.data[CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION] = 1
    build = entity_module.build_provider_request_snapshot

    def configured(*args, **kwargs):
        snapshot = build(*args, **kwargs)
        return replace(
            snapshot, api_kwargs={**snapshot.api_kwargs, "stream": streaming}
        )

    monkeypatch.setattr(entity_module, "build_provider_request_snapshot", configured)
    requests = []

    async def provider(**kwargs):
        requests.append(deepcopy(kwargs))
        if action_first and create.await_count == 1:
            return response(streaming, action=True, finish="tool_calls")
        return response(streaming, answer=answer, refusal=refusal, finish=finish)

    create.side_effect = provider
    entity._execute_function_tool = AsyncMock(
        return_value=make_tool_result_content(
            agent_id=entity.entity_id,
            tool_call_id="action",
            tool_name="effect",
            tool_result={"result": "executed once"},
        )
    )
    tools = [
        {
            "spec": {
                "name": "effect",
                "description": "Effect",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "template", "value_template": "unused"},
        }
    ]
    heard = []
    log.delta_listener = lambda _log, delta: heard.append(delta)
    first_count = 2 if action_first else 1
    expected_error = {
        "length": "token",
        "content_filter": "content filter",
        None: "before a terminal finish reason",
    }.get(finish)
    if refusal:
        expected_error = None
    async with completion_event_scope():
        try:
            if expected_error:
                with pytest.raises(HomeAssistantError, match=expected_error):
                    await entity._async_handle_chat_log(
                        log, tools, [], conditional_continue=False
                    )
            else:
                result = await entity._async_handle_chat_log(
                    log, tools, [], conditional_continue=False
                )
                assert result is None
        except HomeAssistantError as err:
            err.add_note(
                f"provider requests={create.await_count}; ordinary action executions={entity._execute_function_tool.await_count}"
            )
            raise
    assert create.await_count == first_count
    assert entity._execute_function_tool.await_count == int(action_first)
    if expected_error:
        # A failed/incomplete stream must not acquire a completed silent answer.
        assert log.content[-1].role == "tool_result"
        assert not any(
            isinstance(item, conversation.AssistantContent)
            and item.content == ""
            and not item.tool_calls
            for item in log.content
        )
        return
    assert isinstance(log.content[-1], conversation.AssistantContent)
    assert log.content[-1].content == (refusal or answer or "")
    assert not log.unresponded_tool_results
    empty_answers = [
        item
        for item in log.content
        if isinstance(item, conversation.AssistantContent)
        and item.content == ""
        and not item.tool_calls
    ]
    assert len(empty_answers) == int(not refusal and not answer)
    if not refusal and not answer:
        assert not any(delta.get("content") for delta in heard)
    if action_first:
        results = [
            item
            for item in log.content
            if is_tool_result_content(item) and item.tool_call_id == "action"
        ]
        assert len(results) == 1
        assert tool_result_data(results[0]) == {"result": "executed once"}
        assert requests[1].get("tools", []) == []
        assert requests[1]["messages"][-1]["role"] == "tool"
        assert requests[1]["messages"][-1]["tool_call_id"] == "action"
    # Reuse the retained history, including the silent terminal Assistant record.
    log.async_add_user_content(conversation.UserContent(content="Thanks"))
    async with completion_event_scope():
        await entity._async_handle_chat_log(log, tools, [], conditional_continue=False)
    assert create.await_count == first_count + 1
    assert entity._execute_function_tool.await_count == int(action_first)


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
@pytest.mark.parametrize(
    "answer", ["", None, "Done", " "], ids=["empty", "null", "spoken", "whitespace"]
)
@pytest.mark.parametrize("action_first", [False, True], ids=["direct", "after-action"])
async def test_normal_completed_answer_terminates(
    hass, monkeypatch, streaming, answer, action_first
):
    await exercise_normal_chat_completion(
        hass, monkeypatch, streaming, answer, action_first=action_first
    )


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
@pytest.mark.parametrize("finish", ["stop", "content_filter"])
async def test_refusal_after_action_remains_terminal(
    hass, monkeypatch, streaming, finish
):
    await exercise_normal_chat_completion(
        hass, monkeypatch, streaming, None, refusal="I cannot do that.", finish=finish
    )


@pytest.mark.parametrize(
    "streaming,finish",
    [
        (True, "length"),
        (False, "length"),
        (True, "content_filter"),
        (False, "content_filter"),
        (True, None),
    ],
)
async def test_failed_response_after_action_is_not_accepted_as_silence(
    hass, monkeypatch, streaming, finish
):
    await exercise_normal_chat_completion(
        hass, monkeypatch, streaming, None, finish=finish
    )
