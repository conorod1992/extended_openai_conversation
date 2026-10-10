"""Conversation continuity across attachment, compaction and replay boundaries."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.context_summary_performance import (
    _DEFER_CONTEXT_SUMMARY,
    async_apply_context_summary,
)
from custom_components.extended_openai_conversation_responses.context_usage_hardening import (
    estimate_prepared_request,
    estimate_provider_input_tokens,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    make_tool_result_content,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    bind_dispatch_origin,
    record_dispatch,
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
)
from custom_components.extended_openai_conversation_responses.usage import RequestUsage
from homeassistant.components import conversation
from homeassistant.helpers import llm


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_image", "image_url": "data:image/png;base64,"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,"}},
        {"type": "input_file", "file_data": "data:application/pdf;base64,"},
        {"type": "file", "file": {"file_data": "data:application/pdf;base64,"}},
    ],
)
def test_encoded_attachment_size_does_not_destroy_history_budget(part):
    small = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "Describe it"}, part],
        }
    ]
    large = deepcopy(small)

    def grow(value):
        for key, item in value.items():
            if isinstance(item, dict):
                grow(item)
            elif isinstance(item, str) and item.startswith("data:"):
                value[key] += "A" * 400000

    grow(large[0]["content"][1])
    original = deepcopy(large)
    estimate = estimate_provider_input_tokens(large)
    assert estimate == estimate_provider_input_tokens(small)
    assert 2000 < estimate < 10000
    usage = RequestUsage()
    estimate_prepared_request(object(), usage, large, None)
    assert usage.input_tokens == estimate
    assert large == original


async def test_preflight_applies_summary_before_pruning(hass):
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.hass = hass
    entity.subentry = SimpleNamespace(
        data={"context_truncate_strategy": "summarize", "context_threshold": 100}
    )
    entity.entry = SimpleNamespace(
        async_create_task=lambda _, coroutine: asyncio.create_task(coroutine)
    )
    entity._async_summarize_history = AsyncMock(return_value="Preserved older fact")
    content = [conversation.SystemContent(content="system")]
    for i in range(5):
        content.extend(
            [
                conversation.UserContent(content=f"question {i} " * 20),
                conversation.AssistantContent(agent_id="a", content="answer " * 30),
            ]
        )
    chat = SimpleNamespace(conversation_id="c", content=content)
    token = _DEFER_CONTEXT_SUMMARY.set(True)
    try:
        await entity._truncate_message_history(
            chat, observed_input_tokens=1000, model="gpt-4.1", api_mode="responses"
        )
        assert len(chat.content) == 11
        assert await async_apply_context_summary(entity, chat)
        assert any("Preserved older fact" in item.content for item in chat.content)
        assert len(chat.content) < 11
    finally:
        _DEFER_CONTEXT_SUMMARY.reset(token)


@pytest.mark.parametrize(
    "function,delay,blocked",
    [
        ({"type": "knowledge", "operation": "search"}, False, False),
        ({"type": "memory", "operation": "list"}, False, False),
        ({"type": "memory", "operation": "add"}, False, True),
        ({"type": "template", "read_only": True}, False, True),
        ({"type": "knowledge", "operation": "search"}, True, True),
    ],
)
def test_only_verified_reads_are_exempt_from_replay(function, delay, blocked):
    entity = SimpleNamespace()
    args = {"query": "tea towels", **({"delay": {"seconds": 600}} if delay else {})}
    retained = llm.ToolInput(id="first", tool_name="tool", tool_args=args)
    dispatched = llm.ToolInput(id="first", tool_name="tool", tool_args=dict(args))
    with bind_dispatch_origin(dispatched, retained):
        record_dispatch(entity, dispatched, {"function": function})
    result = make_tool_result_content(
        agent_id="a", tool_call_id="first", tool_name="tool", tool_result={"result": {}}
    )
    chat = SimpleNamespace(
        conversation_id="c",
        content=[
            conversation.AssistantContent(agent_id="a", tool_calls=[retained]),
            result,
        ],
    )
    remember_unacknowledged_calls(entity, chat, set())
    assert (
        was_unacknowledged_equivalent(
            entity, chat, llm.ToolInput(id="fresh", tool_name="tool", tool_args=args)
        )
        is blocked
    )
