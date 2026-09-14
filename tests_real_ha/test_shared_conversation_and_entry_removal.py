"""Real-HA acceptance for shared conversation concurrency and permanent removal."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

_WAIT_TIMEOUT = 10


def _agent(hass: HomeAssistant, entry) -> ExtendedOpenAIAgentEntity:
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)
    return agent


def _content_strings(log: conversation.ChatLog) -> list[str]:
    return [
        item.content
        for item in log.content
        if isinstance(getattr(item, "content", None), str)
    ]


def _current_text(log: conversation.ChatLog) -> str:
    return _content_strings(log)[-1]


def _speech(result: conversation.ConversationResult) -> str:
    assert result.response.error_code is None
    return result.response.as_dict()["speech"]["plain"]["speech"]


async def _converse(
    hass: HomeAssistant,
    entry,
    text: str,
    *,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=entry.entry_id,
    )


@pytest.mark.asyncio
async def test_concurrent_requests_on_same_conversation_preserve_both_turns(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping calls on one conversation cannot lose either completed turn."""
    entry = _make_entry("Shared conversation concurrency", include_ai_task=False)
    await _setup_entry(hass, entry)
    agent = _agent(hass, entry)

    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    final_history: list[str] = []

    async def model(log: conversation.ChatLog, **kwargs: Any) -> None:
        del kwargs
        current = _current_text(log)
        if current == "seed shared conversation":
            reply = "ack:seed shared conversation"
        elif current == "concurrent turn A":
            first_entered.set()
            await release_first.wait()
            reply = "ack:concurrent turn A"
        elif current == "concurrent turn B":
            reply = "ack:concurrent turn B"
        elif current == "inspect shared history":
            final_history[:] = _content_strings(log)
            reply = "history inspected"
        else:  # pragma: no cover - defensive assertion for this acceptance test
            raise AssertionError(f"Unexpected conversation text: {current}")

        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content=reply,
            )
        )

    monkeypatch.setattr(agent, "_async_handle_chat_log", model)

    seed = await _converse(hass, entry, "seed shared conversation")
    shared_id = seed.conversation_id
    assert shared_id is not None
    assert _speech(seed) == "ack:seed shared conversation"

    first = asyncio.create_task(
        _converse(
            hass,
            entry,
            "concurrent turn A",
            conversation_id=shared_id,
        )
    )
    await asyncio.wait_for(first_entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not first.done()

    # Start a second public Assist call against the same established conversation
    # while A is still blocked. Home Assistant may serialize access internally or
    # allow both handlers to overlap; the integration must be correct either way.
    second = asyncio.create_task(
        _converse(
            hass,
            entry,
            "concurrent turn B",
            conversation_id=shared_id,
        )
    )
    await asyncio.sleep(0)
    assert not first.done()

    release_first.set()
    first_result, second_result = await asyncio.wait_for(
        asyncio.gather(first, second), timeout=_WAIT_TIMEOUT
    )

    assert first_result.conversation_id == shared_id
    assert second_result.conversation_id == shared_id
    assert {_speech(first_result), _speech(second_result)} == {
        "ack:concurrent turn A",
        "ack:concurrent turn B",
    }

    # Read the next turn through the same public conversation ID. Both completed
    # concurrent turns and their assistant replies must be represented exactly
    # once in the durable/current chat history; otherwise one request overwrote or
    # duplicated sibling continuity state.
    final = await _converse(
        hass,
        entry,
        "inspect shared history",
        conversation_id=shared_id,
    )
    assert _speech(final) == "history inspected"
    assert final.conversation_id == shared_id

    for marker in (
        "seed shared conversation",
        "ack:seed shared conversation",
        "concurrent turn A",
        "ack:concurrent turn A",
        "concurrent turn B",
        "ack:concurrent turn B",
        "inspect shared history",
    ):
        assert final_history.count(marker) == 1


@pytest.mark.asyncio
async def test_permanent_entry_removal_during_inflight_request_never_resurrects_runtime(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request from a permanently removed entry may finish but cannot resurrect it."""
    entry = _make_entry("Remove during request", include_ai_task=False)
    await _setup_entry(hass, entry)
    old_agent = _agent(hass, entry)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_model(log: conversation.ChatLog, **kwargs: Any) -> None:
        del kwargs
        current = _current_text(log)
        entered.set()
        await release.wait()
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=old_agent.entity_id,
                content=f"old-generation:{current}",
            )
        )

    monkeypatch.setattr(old_agent, "_async_handle_chat_log", blocking_model)

    old_request = asyncio.create_task(
        _converse(hass, entry, "request crossing permanent removal")
    )
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not old_request.done()

    # Exercise Home Assistant's permanent config-entry removal path rather than a
    # reload/unload. Removal must tear down registration and persistent ownership
    # while allowing the already-running Python object to unwind safely.
    await hass.config_entries.async_remove(entry.entry_id)
    await asyncio.wait_for(hass.async_block_till_done(), timeout=_WAIT_TIMEOUT)

    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert conversation.async_get_agent(hass, entry.entry_id) is None
    assert not old_request.done()

    rows_after_remove = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    assert not rows_after_remove

    release.set()
    result = await asyncio.wait_for(old_request, timeout=_WAIT_TIMEOUT)
    assert _speech(result) == "old-generation:request crossing permanent removal"

    # Completion of the stale generation must not re-register an agent or restore
    # registry ownership after Home Assistant has permanently deleted the entry.
    await hass.async_block_till_done()
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert conversation.async_get_agent(hass, entry.entry_id) is None
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
