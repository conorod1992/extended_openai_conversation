"""Provider adapter contracts for inconsistent streams and attachment handoff."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from openai.types.responses import ResponseOutputMessage
import pytest

from custom_components.extended_openai_conversation_responses import entity as module
from custom_components.extended_openai_conversation_responses.exceptions import (
    ParseArgumentsFailed,
)
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
)
from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from tests.test_entity_remaining_branches import _entity, _Stream


def _item_event(kind="added", *, index=0, item_id="item", name="demo", arguments="{}"):
    return SimpleNamespace(
        type=f"response.output_item.{kind}",
        output_index=index,
        item=SimpleNamespace(
            type="function_call",
            id=item_id,
            call_id="call",
            name=name,
            arguments=arguments,
        ),
    )


def _argument_event(kind="delta", *, item_id="item", text="{}"):
    return SimpleNamespace(
        type=f"response.function_call_arguments.{kind}",
        output_index=0,
        item_id=item_id,
        delta=text,
        arguments=text,
    )


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (
            [
                SimpleNamespace(
                    type="response.completed", response=SimpleNamespace(usage=None)
                ),
                SimpleNamespace(type="response.output_text.delta", delta="late"),
            ],
            "after terminal",
        ),
        ([_item_event(), _item_event(item_id="other")], "conflicting output item"),
        ([_item_event(), _item_event(index=1)], "duplicate output item id"),
        (
            [
                _item_event(),
                _argument_event(text="{}"),
                _item_event("done", arguments='{"changed":true}'),
            ],
            "conflicting tool arguments",
        ),
        (
            [_item_event(), _argument_event(item_id="other")],
            "conflicting argument item",
        ),
        (
            [
                _argument_event(text="{}"),
                _argument_event("done", text='{"changed":true}'),
            ],
            "conflicting tool arguments",
        ),
        ([_item_event("done"), _item_event("done")], "repeated completed output item"),
    ],
    ids=[
        "late-event",
        "changed-identity",
        "duplicate-identity",
        "changed-final-arguments",
        "wrong-argument-owner",
        "changed-argument-done",
        "duplicate-completion",
    ],
)
async def test_responses_rejects_inconsistent_stream_before_dispatch(events, message):
    with pytest.raises(ProviderStreamError, match=message) as caught:
        _ = [
            delta
            async for delta in _entity()._transform_responses_stream(
                SimpleNamespace(), _Stream(events)
            )
        ]
    assert caught.value.type == "invalid_event_sequence"


def _chat_chunk(*, name="demo", arguments="{}", finish=None, content=None):
    return SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason=finish,
                delta=SimpleNamespace(
                    content=content,
                    refusal=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="call",
                            function=SimpleNamespace(name=name, arguments=arguments),
                        )
                    ]
                    if name
                    else None,
                ),
            )
        ],
    )


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        (
            [
                _chat_chunk(name=None, finish="stop"),
                _chat_chunk(name=None, content="late"),
            ],
            "after terminal",
        ),
        (
            [_chat_chunk(name="first"), _chat_chunk(name="second")],
            "conflicting tool name",
        ),
    ],
    ids=["late-chat-event", "changed-tool-name"],
)
async def test_chat_rejects_inconsistent_stream_before_dispatch(chunks, message):
    with pytest.raises(ProviderStreamError, match=message):
        _ = [
            delta
            async for delta in _entity()._transform_chat_stream(
                SimpleNamespace(), _Stream(chunks)
            )
        ]


@pytest.mark.parametrize(
    "name",
    [module.CONTINUE_CONVERSATION_TOOL_NAME, module.FUNCTION_GROUP_LOADER_TOOL_NAME],
)
@pytest.mark.parametrize("api_mode", ["chat", "responses"])
async def test_control_tools_require_object_arguments(name, api_mode):
    instance = _entity()
    stream = (
        instance._transform_chat_stream(
            SimpleNamespace(),
            _Stream([_chat_chunk(name=name, arguments="[]", finish="tool_calls")]),
        )
        if api_mode == "chat"
        else instance._transform_responses_stream(
            SimpleNamespace(), _Stream([_item_event("done", name=name, arguments="[]")])
        )
    )
    with pytest.raises(ParseArgumentsFailed):
        _ = [delta async for delta in stream]


@pytest.mark.parametrize(
    "user",
    [
        conversation.UserContent(content="text"),
        conversation.AssistantContent(agent_id="agent", content="reply"),
    ],
)
async def test_prepare_attachments_without_files_preserves_request_budget(hass, user):
    instance = _entity()
    instance.hass = hass
    message = {"role": "user", "content": "unchanged"}
    assert (
        await instance._async_prepare_user_attachments(
            user, message, "responses", total_bytes=17
        )
        == 17
    )
    assert message == {"role": "user", "content": "unchanged"}
    hass.async_add_executor_job.assert_not_awaited()


async def test_prepare_single_turn_attachment_limit_rejects_before_read(hass):
    instance = _entity()
    instance.hass = hass
    user = conversation.UserContent(
        content="photo",
        attachments=[SimpleNamespace(path="unused", mime_type="image/png")]
        * (module.MAX_ATTACHMENT_COUNT + 1),
    )
    with pytest.raises(HomeAssistantError, match="At most"):
        await instance._async_prepare_user_attachments(
            user, {"role": "user", "content": "photo"}, "responses"
        )
    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.parametrize(
    "structure_schema",
    [None, {"type": "object", "properties": {"answer": {"type": "string"}}}],
)
async def test_native_schema_is_rejected_for_unsupported_model(
    hass, monkeypatch, structure_schema
):
    instance = _entity()
    instance.hass = hass
    instance.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock())
    )
    monkeypatch.setattr(
        module,
        "build_provider_request_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            api_kwargs={"model": "unsupported"},
            api_mode="responses",
            provider_tools=[],
            structured_outputs=False,
        ),
    )
    log = conversation.ChatLog(hass, "conversation")
    log.content[0] = conversation.SystemContent(content="system")
    log.async_add_user_content(conversation.UserContent(content="question"))
    with pytest.raises(
        HomeAssistantError, match="does not support native Structured Outputs"
    ):
        await instance._async_handle_chat_log(
            log, [], [], structure=object(), structure_schema=structure_schema
        )
    instance.entry.runtime_data.responses.create.assert_not_awaited()


@pytest.mark.parametrize("part", ["image", "pdf"])
async def test_chat_attachment_handoff_converts_images_and_rejects_pdf(
    hass, monkeypatch, part
):
    instance = _entity()
    instance.hass = hass
    instance.entity_id = "conversation.attachment"
    create = AsyncMock(side_effect=ValueError("stop after capturing provider request"))
    instance.entry.runtime_data = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(
        module,
        "build_provider_request_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            api_kwargs={"model": "test"}, api_mode="chat_completions", provider_tools=[]
        ),
    )
    log = conversation.ChatLog(hass, "conversation")
    log.content[0] = conversation.SystemContent(content="system")
    log.async_add_user_content(conversation.UserContent(content="question"))

    async def prepare(_log, messages, _mode):
        messages[-1]["content"] = [
            {"type": "input_text", "text": "question"},
            {"type": "input_image", "image_url": "data:image/png;base64,YQ=="}
            if part == "image"
            else {
                "type": "input_file",
                "file_data": "data:application/pdf;base64,YQ==",
            },
        ]

    instance._async_add_attachments = prepare
    expected = ProviderStreamError if part == "image" else HomeAssistantError
    with pytest.raises(expected):
        await instance._async_handle_chat_log(log, [], [])
    if part == "image":
        assert create.await_args.kwargs["messages"][-1]["content"] == [
            {"type": "text", "text": "question"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
        ]
    else:
        create.assert_not_awaited()


async def test_responses_tool_prompt_is_inserted_and_removed_without_losing_user_history(
    hass, monkeypatch
):
    instance = _entity()
    instance.hass = hass
    instance.entity_id = "conversation.prompt"
    snapshots = []
    completed = SimpleNamespace(
        type="response.completed", response=SimpleNamespace(usage=None)
    )
    rounds = iter(
        [
            _Stream(
                [
                    _item_event(
                        "added",
                        name=module.FUNCTION_GROUP_LOADER_TOOL_NAME,
                        arguments='{"groups":["lighting"]}',
                    ),
                    _item_event(
                        "done",
                        name=module.FUNCTION_GROUP_LOADER_TOOL_NAME,
                        arguments='{"groups":["lighting"]}',
                    ),
                    completed,
                ]
            ),
            _Stream(
                [
                    SimpleNamespace(
                        type="response.output_item.added",
                        item=SimpleNamespace(type="message"),
                    ),
                    SimpleNamespace(type="response.output_text.delta", delta="Done"),
                    completed,
                ]
            ),
        ]
    )

    async def create(**kwargs):
        snapshots.append(deepcopy(kwargs["input"]))
        return next(rounds)

    instance.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(create=create)
    )
    monkeypatch.setattr(
        module,
        "build_provider_request_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            api_kwargs={"model": "test"}, api_mode="responses", provider_tools=[]
        ),
    )
    monkeypatch.setattr(
        module, "current_snapshot", lambda: SimpleNamespace(prompt_for=prompt)
    )
    prompt = Mock(side_effect=["HA tool instructions", ""])
    log = conversation.ChatLog(hass, "conversation")
    log.content[0] = conversation.SystemContent(content="")
    log.async_add_user_content(conversation.UserContent(content="Turn on the light"))
    loader = {
        "spec": {
            "name": module.FUNCTION_GROUP_LOADER_TOOL_NAME,
            "description": "Load groups",
            "parameters": {
                "type": "object",
                "properties": {
                    "groups": {"type": "array", "items": {"type": "string"}}
                },
            },
        },
        "function": {"type": "function_group_loader"},
    }
    loaded = Mock(return_value={"status": "loaded"})
    await instance._async_handle_chat_log(
        log, [loader], [], function_group_loader=loaded
    )
    loaded.assert_called_once_with(["lighting"])
    assert snapshots[0][0]["role"] == "system"
    assert snapshots[0][0]["content"] == "HA tool instructions"
    assert not any(item.get("role") == "system" for item in snapshots[1])
    assert any(
        item.get("role") == "user" and item["content"] == "Turn on the light"
        for item in snapshots[1]
    )
    assert log.content[-1].content == "Done"


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_supplied_structured_schema_is_copied_and_sent_for_both_transport_modes(
    hass, monkeypatch, api_mode, streaming
):
    instance = _entity()
    instance.hass = hass
    instance.entity_id = "conversation.structured"
    text = '{"answer":"ok"}'
    completed = SimpleNamespace(
        type="response.completed", response=SimpleNamespace(usage=None)
    )
    responses = (
        _Stream(
            [
                SimpleNamespace(
                    type="response.output_item.added",
                    item=SimpleNamespace(type="message"),
                ),
                SimpleNamespace(type="response.output_text.delta", delta=text),
                completed,
            ]
        )
        if streaming
        else SimpleNamespace(
            output=[
                ResponseOutputMessage(
                    type="message",
                    id="message",
                    role="assistant",
                    status="completed",
                    content=[{"type": "output_text", "text": text, "annotations": []}],
                )
            ],
            status="completed",
            usage=None,
        )
    )
    chat = (
        _Stream([_chat_chunk(name=None, content=text, finish="stop")])
        if streaming
        else SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=text, tool_calls=None, refusal=None
                    ),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
    )
    responses_create, chat_create = (
        AsyncMock(return_value=responses),
        AsyncMock(return_value=chat),
    )
    instance.entry.runtime_data = SimpleNamespace(
        responses=SimpleNamespace(create=responses_create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
    )
    monkeypatch.setattr(
        module,
        "build_provider_request_snapshot",
        lambda *_args, **_kwargs: SimpleNamespace(
            api_kwargs={"model": "test", "stream": streaming},
            api_mode=api_mode,
            provider_tools=[],
            structured_outputs=True,
        ),
    )
    formatter = Mock(side_effect=AssertionError("the supplied schema is authoritative"))
    monkeypatch.setattr(module, "_format_structured_output", formatter)
    log = conversation.ChatLog(hass, "conversation")
    log.content[0] = conversation.SystemContent(content="system")
    log.async_add_user_content(conversation.UserContent(content="question"))
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    original = deepcopy(schema)
    await instance._async_handle_chat_log(
        log,
        [],
        [],
        structure_name="Result",
        structure=object(),
        structure_schema=schema,
    )
    request = (
        responses_create if api_mode == "responses" else chat_create
    ).await_args.kwargs
    formatted = (
        request["text"]["format"]
        if api_mode == "responses"
        else request["response_format"]["json_schema"]
    )
    assert formatted["schema"]["additionalProperties"] is False
    assert formatted["schema"]["required"] == ["answer"]
    assert schema == original
    assert log.content[-1].content == text
    formatter.assert_not_called()
