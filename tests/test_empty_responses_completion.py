"""No-output Responses preserve their contract and fail boundedly after Tools."""

from dataclasses import replace
from unittest.mock import AsyncMock

from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseIncompleteEvent,
)
import pytest

from custom_components.extended_openai_conversation_responses import entity as owner
from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    is_tool_result_content,
    make_tool_result_content,
    tool_result_data,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_responses_api import _control_entity
from tests.test_responses_history_coherence import call, events, message, reasoning


def response(streaming, items, status="completed"):
    result = Response(
        id="response",
        created_at=0,
        model="gpt-4o-mini",
        object="response",
        output=items,
        parallel_tool_calls=False,
        tool_choice="auto",
        tools=[],
        status=status,
        usage=None,
        incomplete_details={"reason": "max_output_tokens"}
        if status == "incomplete"
        else None,
        error={"code": "server_error", "message": "provider failed"}
        if status == "failed"
        else None,
    )
    if not streaming:
        return result
    stream = events(items, complete=False)
    event = {
        "completed": ResponseCompletedEvent,
        "incomplete": ResponseIncompleteEvent,
        "failed": ResponseFailedEvent,
    }.get(status)
    if event:
        stream.events.append(
            event(
                type="response." + status,
                response=result,
                sequence_number=len(stream.events),
            )
        )
    return stream


async def exercise_empty_responses_completion(
    hass,
    monkeypatch,
    streaming,
    shape="empty",
    *,
    action_first=True,
    status="completed",
    conditional=False,
):
    entity, log, create = _control_entity(hass, "responses", [])
    entity.subentry.data["max_function_calls_per_conversation"] = 1
    build = owner.build_provider_request_snapshot

    def configured(*args, **kwargs):
        snapshot = build(*args, **kwargs)
        return replace(
            snapshot, api_kwargs={**snapshot.api_kwargs, "stream": streaming}
        )

    monkeypatch.setattr(owner, "build_provider_request_snapshot", configured)
    shapes = {
        "empty": [],
        "message": [message("msg", "")],
        "reasoning": [reasoning("rs")],
        "spoken": [message("spoken", "Done")],
    }
    items = shapes[shape]

    async def provider(**kwargs):
        assert kwargs["stream"] is streaming
        if action_first and create.await_count == 1:
            return response(streaming, [call("effect", "action")])
        return response(streaming, items, status)

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
    expected_error = {
        "incomplete": "token",
        "failed": "response failed",
        "in_progress": "before a terminal event",
    }.get(status)
    if status == "completed" and shape == "empty" and action_first:
        expected_error = "completed without assistant output after tool results"
    async with completion_event_scope():
        try:
            if expected_error:
                with pytest.raises(
                    ProviderStreamError if status == "failed" else HomeAssistantError,
                ) as failure:
                    await entity._async_handle_chat_log(
                        log, tools, [], conditional_continue=conditional
                    )
            else:
                result = await entity._async_handle_chat_log(
                    log, tools, [], conditional_continue=conditional
                )
                assert result is None
        except HomeAssistantError as err:
            err.add_note(
                f"provider requests={create.await_count}; ordinary action executions={entity._execute_function_tool.await_count}"
            )
            raise
    expected_requests = int(action_first) + (2 if conditional else 1)
    assert create.await_count == expected_requests, (
        f"provider requests={create.await_count}; ordinary action executions={entity._execute_function_tool.await_count}"
    )
    assert entity._execute_function_tool.await_count == int(action_first)
    if expected_error:
        assert expected_error in str(failure.value)
        assert log.content[-1].role == "tool_result"
        results = [
            item
            for item in log.content
            if is_tool_result_content(item) and item.tool_call_id == "action"
        ]
        assert len(results) == 1 and tool_result_data(results[0]) == {
            "result": "executed once"
        }
        assert not any(
            isinstance(item, conversation.AssistantContent)
            and item.content == ""
            and not item.tool_calls
            for item in log.content
        )
        return
    if shape == "empty":
        assert log.content[-1].role == "user"
        assert not log.unresponded_tool_results
        return
    assert isinstance(log.content[-1], conversation.AssistantContent)
    assert not log.unresponded_tool_results
    assert log.content[-1].content == ("Done" if shape == "spoken" else None)
    native = [
        item.native
        for item in log.content
        if isinstance(item, conversation.AssistantContent) and item.native
    ]
    assert all(item in native for item in items)
    if action_first:
        results = [
            item
            for item in log.content
            if is_tool_result_content(item) and item.tool_call_id == "action"
        ]
        assert len(results) == 1 and tool_result_data(results[0]) == {
            "result": "executed once"
        }
    log.async_add_user_content(conversation.UserContent(content="Thanks"))
    async with completion_event_scope():
        await entity._async_handle_chat_log(log, tools, [], conditional_continue=False)
    assert create.await_count == expected_requests + 1
    assert entity._execute_function_tool.await_count == int(action_first)


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
@pytest.mark.parametrize("action_first", [False, True], ids=["direct", "after-action"])
@pytest.mark.parametrize("shape", ["empty", "message", "reasoning", "spoken"])
async def test_responses_completed_output_terminates(
    hass, monkeypatch, streaming, action_first, shape
):
    await exercise_empty_responses_completion(
        hass, monkeypatch, streaming, shape, action_first=action_first
    )


@pytest.mark.parametrize(
    "streaming,status",
    [
        (True, "incomplete"),
        (False, "incomplete"),
        (True, "failed"),
        (False, "failed"),
        (True, "in_progress"),
    ],
)
async def test_failed_responses_are_not_accepted_as_silence(
    hass, monkeypatch, streaming, status
):
    await exercise_empty_responses_completion(
        hass, monkeypatch, streaming, status=status
    )


@pytest.mark.parametrize("streaming", [True, False], ids=["streamed", "completed"])
async def test_conditional_empty_fallback_is_bounded(hass, monkeypatch, streaming):
    await exercise_empty_responses_completion(
        hass, monkeypatch, streaming, conditional=True
    )


async def test_public_empty_after_tool_is_a_bounded_error(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses.const import (
        EVENT_CONVERSATION_FINISHED,
    )
    from tests.test_conversation_provider_loop_contracts import _provider_fixture, _tool

    entity, client, continuity, logs, invoke = _provider_fixture(
        hass, monkeypatch, [], tools=[_tool("lookup")]
    )

    async def provider(**_kwargs):
        return response(
            True,
            [call("lookup", "action")]
            if client.responses.create.await_count == 1
            else [],
        )

    client.responses.create.side_effect = provider
    entity._execute_function_tool.return_value = make_tool_result_content(
        agent_id=entity.entity_id,
        tool_call_id="action",
        tool_name="lookup",
        tool_result={"result": "retained lookup result"},
    )
    result = await invoke()
    assert result.response.error_code is not None
    assert (
        "completed without assistant output after tool results"
        in result.response.speech["plain"]["speech"]
    )
    assert client.responses.create.await_count == 2
    entity._execute_function_tool.assert_awaited_once()
    continuity.async_record_success.assert_not_awaited()
    continuity.async_release.assert_awaited_once()
    results = [
        item
        for item in logs[0].content
        if is_tool_result_content(item) and item.tool_call_id == "action"
    ]
    assert len(results) == 1
    assert tool_result_data(results[0]) == {"result": "retained lookup result"}
    notifications = [
        item.args[1]
        for item in hass.bus.async_fire.call_args_list
        if item.args[0] == EVENT_CONVERSATION_FINISHED
    ]
    assert len(notifications) == 1 and notifications[0]["status"] == "error"
