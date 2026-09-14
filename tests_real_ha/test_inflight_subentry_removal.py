"""Real-HA acceptance for removing a conversation subentry mid-request."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _speech,
)

_WAIT_TIMEOUT = 10
_MODEL = "gpt-5.6"
_REMOVED_REQUEST_TEXT = "request that outlives its deleted subentry"
_REMOVED_RESPONSE_TEXT = "deleted subentry request completed"


def _subentry(title: str) -> dict[str, Any]:
    """Return one storage-shaped conversation subentry."""
    return {
        "data": {
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: _MODEL,
        },
        "subentry_type": "conversation",
        "title": title,
        "unique_id": None,
    }


def _entry() -> MockConfigEntry:
    """Build one parent entry with the target and surviving conversation siblings."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="In-flight Subentry Removal",
        data={
            CONF_API_KEY: "sk-inflight-subentry-removal",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("Conversation To Remove"),
            _subentry("Conversation Survivor"),
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _subentry_by_title(entry: MockConfigEntry, title: str):
    return next(item for item in entry.subentries.values() if item.title == title)


def _conversation_rows(hass: HomeAssistant, entry: MockConfigEntry):
    return [
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.domain == "conversation"
    ]


def _row_for_subentry(hass: HomeAssistant, entry: MockConfigEntry, subentry_id: str):
    return next(
        row
        for row in _conversation_rows(hass, entry)
        if row.config_subentry_id == subentry_id
    )


def _agent(hass: HomeAssistant, agent_id: str) -> ExtendedOpenAIAgentEntity:
    agent = conversation.async_get_agent(hass, agent_id)
    assert isinstance(agent, ExtendedOpenAIAgentEntity)
    return agent


def _current_text(log: conversation.ChatLog) -> str:
    return next(
        item.content
        for item in reversed(log.content)
        if isinstance(getattr(item, "content", None), str)
    )


async def _converse(
    hass: HomeAssistant,
    agent_id: str,
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
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_removing_exact_subentry_during_request_cannot_resurrect_or_touch_sibling(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted subentry may finish its old request but must stay permanently removed."""
    entry = _entry()
    await _setup_entry(hass, entry)

    removed_subentry = _subentry_by_title(entry, "Conversation To Remove")
    survivor_subentry = _subentry_by_title(entry, "Conversation Survivor")
    removed_row = _row_for_subentry(hass, entry, removed_subentry.subentry_id)
    survivor_row = _row_for_subentry(hass, entry, survivor_subentry.subentry_id)
    removed_entity_id = removed_row.entity_id
    survivor_entity_id = survivor_row.entity_id

    removed_agent = _agent(hass, removed_entity_id)
    _agent(hass, survivor_entity_id)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_model(log: conversation.ChatLog, **kwargs: Any) -> None:
        del kwargs
        current = _current_text(log)
        assert current == _REMOVED_REQUEST_TEXT
        entered.set()
        await release.wait()
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=removed_agent.entity_id,
                content=_REMOVED_RESPONSE_TEXT,
            )
        )

    monkeypatch.setattr(removed_agent, "_async_handle_chat_log", blocking_model)

    removed_request = asyncio.create_task(
        _converse(hass, removed_entity_id, _REMOVED_REQUEST_TEXT)
    )
    await asyncio.wait_for(entered.wait(), timeout=_WAIT_TIMEOUT)
    assert not removed_request.done()

    # Remove the exact subentry whose request is blocked. Home Assistant should be
    # able to update the parent immediately and retain only the untouched sibling.
    assert hass.config_entries.async_remove_subentry(
        entry, removed_subentry.subentry_id
    )
    await asyncio.wait_for(hass.async_block_till_done(), timeout=_WAIT_TIMEOUT)

    assert entry.state is ConfigEntryState.LOADED
    assert removed_subentry.subentry_id not in entry.subentries
    assert survivor_subentry.subentry_id in entry.subentries
    assert not removed_request.done()

    rows_after_removal = _conversation_rows(hass, entry)
    assert not any(
        row.config_subentry_id == removed_subentry.subentry_id
        or row.entity_id == removed_entity_id
        for row in rows_after_removal
    )
    assert conversation.async_get_agent(hass, removed_entity_id) is None

    surviving_rows = [
        row
        for row in rows_after_removal
        if row.config_subentry_id == survivor_subentry.subentry_id
    ]
    assert len(surviving_rows) == 1
    assert surviving_rows[0].entity_id == survivor_entity_id
    survivor_agent = _agent(hass, survivor_entity_id)

    # Prove the surviving sibling is already healthy before the stale request is
    # allowed to finish. Its provider payload must contain no history from the
    # removed subentry.
    before_wire = _install_wire(
        monkeypatch,
        survivor_agent,
        [_chat_sse_text("surviving sibling works while old request is blocked")],
    )
    before = await _converse(
        hass,
        survivor_entity_id,
        "sibling request while deleted request is still blocked",
    )
    assert _speech(before) == "surviving sibling works while old request is blocked"
    assert len(before_wire.requests) == 1
    before_payload = json.dumps(before_wire.requests[0]["body"], ensure_ascii=False)
    assert _REMOVED_REQUEST_TEXT not in before_payload
    assert _REMOVED_RESPONSE_TEXT not in before_payload
    assert not removed_request.done()

    # The request that began while the deleted entity still existed is permitted to
    # complete. Completion must not re-register that entity or otherwise replace the
    # surviving runtime established after removal.
    release.set()
    removed_result = await asyncio.wait_for(removed_request, timeout=_WAIT_TIMEOUT)
    assert _speech(removed_result) == _REMOVED_RESPONSE_TEXT

    assert removed_subentry.subentry_id not in entry.subentries
    final_rows = _conversation_rows(hass, entry)
    assert not any(
        row.config_subentry_id == removed_subentry.subentry_id
        or row.entity_id == removed_entity_id
        for row in final_rows
    )
    assert conversation.async_get_agent(hass, removed_entity_id) is None
    assert conversation.async_get_agent(hass, survivor_entity_id) is survivor_agent

    # Continue the surviving sibling's own conversation after the stale completion.
    # Its valid history should persist while no text from the removed request leaks in.
    after_wire = _install_wire(
        monkeypatch,
        survivor_agent,
        [_chat_sse_text("surviving sibling remains isolated after completion")],
    )
    after = await _converse(
        hass,
        survivor_entity_id,
        "continue the surviving sibling",
        conversation_id=before.conversation_id,
    )
    assert _speech(after) == "surviving sibling remains isolated after completion"
    assert after.conversation_id == before.conversation_id
    assert len(after_wire.requests) == 1
    after_payload = json.dumps(after_wire.requests[0]["body"], ensure_ascii=False)
    assert "sibling request while deleted request is still blocked" in after_payload
    assert "surviving sibling works while old request is blocked" in after_payload
    assert _REMOVED_REQUEST_TEXT not in after_payload
    assert _REMOVED_RESPONSE_TEXT not in after_payload

    final_survivor_rows = [
        row
        for row in _conversation_rows(hass, entry)
        if row.config_subentry_id == survivor_subentry.subentry_id
    ]
    assert len(final_survivor_rows) == 1
    assert final_survivor_rows[0].entity_id == survivor_entity_id
