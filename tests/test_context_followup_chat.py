"""Chat Completions follow-up budgeting preserves complete tool exchanges."""

from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONTEXT_TRUNCATE_KEEP_RECENT,
)
from homeassistant.components import conversation
from tests.test_provider_tool_protocol import (
    _chat_log,
    _entity,
    _final_stream,
    _protocol_messages,
    _result,
    _tool,
    _tool_call_stream,
)


@pytest.mark.parametrize("shorten", [False, True])
async def test_chat_followup_evicts_old_history_and_keeps_call_result_pair(
    hass, shorten
):
    entity = _entity(
        hass,
        [
            _tool_call_stream("call-original-id", "lookup", {}),
            _final_stream("done"),
        ],
    )
    entity.subentry.data.update(
        {
            CONF_CONTEXT_THRESHOLD: 3000,
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_KEEP_RECENT,
            CONF_SHORTEN_TOOL_CALL_ID: shorten,
        }
    )
    chat_log = _chat_log(hass)
    chat_log.content[1:1] = [
        conversation.UserContent(content="old-marker " * 500),
        conversation.AssistantContent(agent_id=entity.entity_id, content="old answer"),
    ]

    async def execute(_tool, tool_input, _context, _entities):
        return _result(entity, tool_input, "large result " * 800)

    entity._execute_function_tool = AsyncMock(side_effect=execute)
    await entity._async_handle_chat_log(chat_log, [_tool("lookup")], [])

    initial, followup = entity._client.chat.completions.create.await_args_list
    assert "old-marker" in str(initial.kwargs["messages"])
    assert "old-marker" not in str(followup.kwargs["messages"])
    calls, results = _protocol_messages(followup.kwargs["messages"])
    assert len(calls) == len(results) == 1
    assert calls[0]["id"] == results[0]["tool_call_id"]
    assert (calls[0]["id"] != "call-original-id") is shorten
    assert "large result" in results[0]["content"]
    entity._execute_function_tool.assert_awaited_once()
    assert chat_log.content[-1].content == "done"


@pytest.mark.parametrize(
    "kind", ["chat_image", "responses_image", "responses_reasoning"]
)
@pytest.mark.parametrize("strategy", ["keep_recent", "summary", "summary_failure"])
async def test_prepared_eviction_preserves_newer_turns(hass, kind, strategy):
    """Removing one costly old turn must not evict cheap newer exchanges."""
    import asyncio
    from openai.types.responses import ResponseReasoningItem
    from tests.test_tool_exchange_protocol import _entity as responses_entity
    from tests.test_tool_exchange_protocol import _final_stream as responses_final

    responses = kind.startswith("responses")
    entity = (
        responses_entity(hass, [responses_final("done")])
        if responses
        else _entity(hass, [_final_stream("done")])
    )
    entity.entry.async_create_task = lambda _hass, coroutine: asyncio.create_task(
        coroutine
    )
    entity.subentry.data.update(
        {
            CONF_CONTEXT_THRESHOLD: 1100,
            CONF_CONTEXT_TRUNCATE_STRATEGY: "summarize"
            if strategy.startswith("summary")
            else "keep_recent",
        }
    )
    entity._async_summarize_history = AsyncMock(
        return_value=None if strategy == "summary_failure" else "Old image summarized"
    )
    log = _chat_log(hass)
    old = conversation.UserContent(content="Old costly turn")
    recent = []
    for index in range(3):
        recent.extend(
            [
                conversation.UserContent(
                    content=f"Recent question {index} " + "x" * 180
                ),
                conversation.AssistantContent(
                    agent_id=entity.entity_id,
                    content=f"Recent answer {index} " + "y" * 180,
                ),
            ]
        )
    log.content[1:1] = [
        old,
        conversation.AssistantContent(agent_id=entity.entity_id, content="Old answer"),
        *recent,
    ]
    if kind.endswith("reasoning"):
        log.content.insert(
            2,
            conversation.AssistantContent(
                agent_id=entity.entity_id,
                native=ResponseReasoningItem(
                    id="rs_old",
                    type="reasoning",
                    summary=[],
                    encrypted_content="r" * 18000,
                ),
            ),
        )
    else:

        async def attach(_log, messages, _api):
            next(item for item in messages if item.get("role") == "user")["content"] = [
                {"type": "input_text", "text": old.content},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ]

        entity._async_add_attachments = attach
    await entity._async_handle_chat_log(log, [], [])
    create = (
        entity._client.responses.create
        if responses
        else entity._client.chat.completions.create
    )
    outgoing = create.await_args.kwargs["input" if responses else "messages"]
    assert "Old costly turn" not in str(outgoing)
    for item in recent:
        assert item in log.content
        assert item.content in str(outgoing)
    if strategy.startswith("summary"):
        entity._async_summarize_history.assert_awaited_once()
        summarized = entity._async_summarize_history.await_args.args[0]
        assert old in summarized
        assert not any(item in summarized for item in recent)
