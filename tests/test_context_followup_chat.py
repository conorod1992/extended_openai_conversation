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
