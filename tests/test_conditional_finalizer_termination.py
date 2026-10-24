"""A completed Conditional answer terminates the provider loop, even in silence."""

import json
from unittest.mock import AsyncMock

from openai.types.chat import ChatCompletionChunk
import pytest

from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)
from custom_components.extended_openai_conversation_responses.entity import (
    CONTINUE_CONVERSATION_TOOL_NAME,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    is_tool_result_content,
    make_tool_result_content,
    tool_result_data,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_responses_api import FakeStream, _control_entity
from tests.test_responses_history_coherence import call, events


def _tool_stream(api_mode, calls):
    if api_mode == "responses":
        return events(
            [call(name, call_id, arguments) for name, call_id, arguments in calls]
        )
    return FakeStream(
        [
            ChatCompletionChunk(
                id="chat-" + calls[0][1],
                object="chat.completion.chunk",
                created=0,
                model="gpt-4o-mini",
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                                for index, (name, call_id, arguments) in enumerate(
                                    calls
                                )
                            ],
                        },
                    }
                ],
            )
        ]
    )


async def exercise_finalizer_termination(
    hass, api_mode, decision, answer, *, action_first=True, premature=False
):
    entity, log, create = _control_entity(hass, api_mode, [])
    entity.subentry.data[CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION] = 1
    final = (
        CONTINUE_CONVERSATION_TOOL_NAME,
        "final",
        {
            "response": answer,
            "continue_conversation": decision,
        },
    )

    async def provider(**_kwargs):
        if action_first and create.await_count == 1:
            calls = [("effect", "action", {})]
            if premature:
                calls.append(
                    (
                        CONTINUE_CONVERSATION_TOOL_NAME,
                        "premature",
                        {
                            "response": "",
                            "continue_conversation": not decision,
                        },
                    )
                )
            return _tool_stream(api_mode, calls)
        # Keep supplying the same valid final answer if the loop incorrectly
        # asks again, reproducing the real 64-request guard rather than an
        # exhausted mock response list.
        return _tool_stream(api_mode, [final])

    create.side_effect = provider
    entity._execute_function_tool = AsyncMock(
        return_value=make_tool_result_content(
            agent_id=entity.entity_id,
            tool_call_id="action",
            tool_name="effect",
            tool_result={"result": "effect completed once"},
        )
    )
    tools = [
        {
            "spec": {
                "name": "effect",
                "description": "Effect",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            "function": {"type": "template", "value_template": "unused"},
        }
    ]
    heard = []
    log.delta_listener = lambda _log, delta: heard.append(delta)
    async with completion_event_scope():
        try:
            result = await entity._async_handle_chat_log(
                log, tools, [], conditional_continue=True
            )
        except HomeAssistantError as err:
            err.add_note(
                f"provider requests={create.await_count}; "
                f"ordinary action executions={entity._execute_function_tool.await_count}"
            )
            raise
    assert result is decision
    assert create.await_count == (2 if action_first else 1)
    assert entity._execute_function_tool.await_count == int(action_first)
    assert isinstance(log.content[-1], conversation.AssistantContent)
    assert log.content[-1].content == answer
    assert not log.unresponded_tool_results
    empty_answers = [
        item
        for item in log.content
        if isinstance(item, conversation.AssistantContent)
        and item.content == ""
        and not item.tool_calls
    ]
    assert len(empty_answers) == int(answer == "")
    speech = "".join(delta.get("content", "") for delta in heard)
    assert speech == answer
    if action_first:
        request_tools = create.await_args_list[1].kwargs["tools"]
        assert {
            tool["name"] if api_mode == "responses" else tool["function"]["name"]
            for tool in request_tools
        } == {CONTINUE_CONVERSATION_TOOL_NAME}
        action_results = [
            item
            for item in log.content
            if is_tool_result_content(item) and item.tool_call_id == "action"
        ]
        assert len(action_results) == 1
        assert action_results[0].tool_name == "effect"
        assert tool_result_data(action_results[0]) == {
            "result": "effect completed once"
        }


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("decision", [False, True])
@pytest.mark.parametrize("answer", ["", "Done"], ids=["silent", "spoken"])
@pytest.mark.parametrize("action_first", [False, True], ids=["direct", "after-action"])
async def test_completed_finalizer_terminates(
    hass, api_mode, decision, answer, action_first
):
    await exercise_finalizer_termination(
        hass, api_mode, decision, answer, action_first=action_first
    )


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("decision", [False, True])
async def test_premature_silence_does_not_apply_an_answer(hass, api_mode, decision):
    await exercise_finalizer_termination(
        hass, api_mode, decision, "Done", premature=True
    )
