"""Tests for post-turn context summarization."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.components import conversation

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONTEXT_TRUNCATE_SUMMARIZE,
)
from custom_components.extended_openai_conversation_responses.context import (
    partition_history,
)
from custom_components.extended_openai_conversation_responses.context_summary import (
    DeferredContextSummaryManager,
)
from custom_components.extended_openai_conversation_responses.context_summary_performance import (
    _DEFER_CONTEXT_SUMMARY,
    install_deferred_context_summary,
)
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)


def _history(turns: int = 5) -> list[conversation.Content]:
    content: list[conversation.Content] = [
        conversation.SystemContent(content="System prompt")
    ]
    for number in range(turns):
        content.extend(
            [
                conversation.UserContent(content=f"User turn {number} " * 20),
                conversation.AssistantContent(
                    agent_id="conversation.test",
                    content=f"Assistant turn {number} " * 20,
                ),
            ]
        )
    return content


async def test_manager_schedules_without_waiting_and_applies_before_followup() -> None:
    manager = DeferredContextSummaryManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(_older, _model, _api_mode):
        started.set()
        await release.wait()
        return "The user chose Celsius."

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    assert content == _history()
    await asyncio.wait_for(started.wait(), timeout=1)

    content.append(conversation.UserContent(content="What about tomorrow?"))
    apply_task = asyncio.create_task(manager.async_apply("conversation:test", content))
    await asyncio.sleep(0)
    assert not apply_task.done()

    release.set()
    assert await asyncio.wait_for(apply_task, timeout=1)
    assert isinstance(content[0], conversation.SystemContent)
    assert isinstance(content[1], conversation.SystemContent)
    assert "Celsius" in content[1].content
    assert content[-1].content == "What about tomorrow?"


async def test_cancelled_followup_keeps_pending_summary_for_next_attempt() -> None:
    manager = DeferredContextSummaryManager()
    started = asyncio.Event()
    release = asyncio.Event()

    async def summarize(_older, _model, _api_mode):
        started.set()
        await release.wait()
        return "The user chose Celsius."

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    content.append(conversation.UserContent(content="First followup"))

    apply_task = asyncio.create_task(manager.async_apply("conversation:test", content))
    await asyncio.sleep(0)
    apply_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await apply_task

    release.set()
    assert await manager.async_apply("conversation:test", content)
    assert "Celsius" in content[1].content
    assert content[-1].content == "First followup"


async def test_manager_summary_failure_uses_existing_keep_recent_fallback() -> None:
    manager = DeferredContextSummaryManager()

    async def summarize(_older, _model, _api_mode):
        return None

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    content.append(conversation.UserContent(content="Newest question"))

    assert await manager.async_apply("conversation:test", content)
    assert len(partition_history(content[:-1]).turns) == 1
    assert "User turn 4" in content[-3].content
    assert content[-1].content == "Newest question"


async def test_manager_does_not_overwrite_changed_history() -> None:
    manager = DeferredContextSummaryManager()

    async def summarize(_older, _model, _api_mode):
        return "Summary"

    content = _history()
    assert manager.schedule(
        "conversation:test",
        content,
        observed_input_tokens=1000,
        target_tokens=100,
        model="gpt-5.6-luna",
        api_mode=API_MODE_RESPONSES,
        summarize=summarize,
        scheduler=asyncio.create_task,
    )
    content[2] = conversation.AssistantContent(
        agent_id="conversation.test", content="Externally changed"
    )
    current = list(content)

    assert not await manager.async_apply("conversation:test", content)
    assert content == current


async def test_live_truncation_hook_returns_before_summary_provider_finishes() -> None:
    install_deferred_context_summary()
    started = asyncio.Event()
    release = asyncio.Event()

    async def create_summary(**_kwargs):
        started.set()
        await release.wait()
        return SimpleNamespace(output_text="The user chose Celsius.", usage=None)

    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(side_effect=create_summary))
    )
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.entry = SimpleNamespace(
        runtime_data=client,
        data={},
        async_create_task=lambda _hass, coroutine: asyncio.create_task(coroutine),
    )
    entity.subentry = SimpleNamespace(
        data={
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_SUMMARIZE,
            CONF_CONTEXT_THRESHOLD: 100,
            CONF_CHAT_MODEL: "gpt-5.6-luna",
            CONF_API_MODE: API_MODE_RESPONSES,
        }
    )
    entity._usage = None
    entity.__dict__["hass"] = SimpleNamespace()
    chat_log = SimpleNamespace(conversation_id="conversation:test", content=_history())
    original = list(chat_log.content)

    token = _DEFER_CONTEXT_SUMMARY.set(True)
    try:
        await asyncio.wait_for(
            entity._truncate_message_history(
                chat_log,
                observed_input_tokens=1000,
                model="gpt-5.6-luna",
                api_mode=API_MODE_RESPONSES,
            ),
            timeout=1,
        )
    finally:
        _DEFER_CONTEXT_SUMMARY.reset(token)

    assert chat_log.content == original
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    assert await DeferredContextSummaryManager.async_apply(
        entity._deferred_context_summary_manager,
        "conversation:test",
        chat_log.content,
    )
    assert "Celsius" in chat_log.content[1].content
