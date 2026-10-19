"""Budget the dispatched API and never reuse counts from discarded history."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.entity import ExtendedOpenAIBaseLLMEntity
from homeassistant.components import conversation
from tests.test_responses_api import FakeStream, _event, _completed_event, _function_call
from tests.test_provider_stream_terminals import _chat_chunk


def _entity(hass, api_mode, create, threshold):
    entity = object.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass
    entity.entity_id = "conversation.budget"
    entity.entry = SimpleNamespace(data={}, runtime_data=SimpleNamespace(
        responses=SimpleNamespace(create=create),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    entity.subentry = SimpleNamespace(data={"chat_model": "gpt-4.1", "api_mode": api_mode,
        "context_threshold": threshold, "context_truncate_strategy": "keep_recent"})
    entity._usage = None
    return entity


async def test_chat_budget_omits_retained_encrypted_responses_items(hass):
    create = AsyncMock(return_value=FakeStream([_chat_chunk(content="Done", finish_reason="stop")]))
    entity = _entity(hass, "chat_completions", create, 500)
    log = conversation.ChatLog(hass, "budget-switch")
    log.content[0] = conversation.SystemContent(content="System")
    log.async_add_user_content(conversation.UserContent(content="Remember my earlier question"))
    log.async_add_assistant_content_without_tools(conversation.AssistantContent(
        agent_id=entity.entity_id, content=None,
        native=SimpleNamespace(type="reasoning", model_dump=lambda **_: {
            "type": "reasoning", "id": "rs_old", "summary": [], "encrypted_content": "x" * 10000})))
    log.async_add_assistant_content_without_tools(conversation.AssistantContent(agent_id=entity.entity_id, content="Earlier answer"))
    log.async_add_user_content(conversation.UserContent(content="Follow-up"))
    original = list(log.content)
    await entity._async_handle_chat_log(log, [], [])
    assert log.content[:len(original)] == original
    messages = create.await_args.kwargs["messages"]
    assert any(item.get("content") == "Remember my earlier question" for item in messages)
    assert "encrypted_content" not in str(messages)
