"""Real-HA acceptance for Temporary Memory across config-entry reload."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_USER,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
    async_read_temporary_memory_snapshot,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_real_ha.test_temporary_memory_lifecycle import (
    _MEMORY_TEXT,
    _OWNER_ID,
    _OWNER_SCOPE,
    _chat_sse_temporary_memory_add,
    _say,
    _system_prompt,
    _temporary_tool_result,
)

_SECOND_MEMORY_TEXT = "The temporary fallback colour is amber-gold."


@pytest.mark.asyncio
async def test_temporary_memory_survives_real_entry_unload_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored temporary context survives reload and remains mutable afterward."""
    method_names = (
        "async_initialize",
        "async_active",
        "async_active_snapshot",
        "_async_save_locked",
    )
    owners_before_setup = {
        name: getattr(TemporaryMemory, name) for name in method_names
    }
    MockUser(id=_OWNER_ID, name="Temporary Memory Owner").add_to_hass(hass)
    entry = _make_entry(
        "Temporary Memory Reload",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)

    old_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert old_agent is not None
    assert old_agent._temporary_memory is not None
    conversation_subentry = next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )

    expires_at = (dt_util.utcnow() + timedelta(hours=2)).isoformat()
    create_wire = _install_wire(
        monkeypatch,
        old_agent,
        [
            _chat_sse_temporary_memory_add(expires_at),
            _chat_sse_text("I will remember that temporarily."),
        ],
    )
    created_turn = await _say(
        hass,
        old_agent,
        _OWNER_ID,
        "Remember my temporary launch colour.",
    )

    assert _speech(created_turn) == "I will remember that temporarily."
    created = _temporary_tool_result(create_wire.requests[1]["body"])
    assert created["status"] == "created"
    original_memory_id = created["memory"]["memory_id"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is None

    # Read through a fresh HA Store adapter while the entity is absent. This proves
    # the record is durable storage, not merely state retained by the cached manager.
    stored_while_unloaded = await async_read_temporary_memory_snapshot(
        hass,
        entry.entry_id,
        conversation_subentry.subentry_id,
        _OWNER_SCOPE,
        _OWNER_SCOPE,
    )
    assert [record.memory_id for record in stored_while_unloaded] == [
        original_memory_id
    ]
    assert stored_while_unloaded[0].content == _MEMORY_TEXT

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    reloaded_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert reloaded_agent is not None
    assert reloaded_agent is not old_agent
    assert reloaded_agent._temporary_memory is not None

    recall_wire = _install_wire(
        monkeypatch,
        reloaded_agent,
        [_chat_sse_text("Your temporary launch colour is glacier-teal.")],
    )
    recall_turn = await _say(
        hass,
        reloaded_agent,
        _OWNER_ID,
        "What temporary launch colour are you holding?",
        conversation_id=created_turn.conversation_id,
    )

    assert _speech(recall_turn) == "Your temporary launch colour is glacier-teal."
    assert _MEMORY_TEXT in _system_prompt(recall_wire.requests[0]["body"])

    # The recreated entity must not treat restored state as read-only or poisoned.
    second_expiry = (dt_util.utcnow() + timedelta(hours=3)).isoformat()
    added = await reloaded_agent._temporary_memory.async_add(
        _OWNER_SCOPE,
        _SECOND_MEMORY_TEXT,
        second_expiry,
        category="acceptance",
        owner_scope_id=_OWNER_SCOPE,
    )
    assert added["status"] == "created"
    second_memory_id = added["memory"]["memory_id"]
    assert second_memory_id != original_memory_id

    stored_after_mutation = await async_read_temporary_memory_snapshot(
        hass,
        entry.entry_id,
        conversation_subentry.subentry_id,
        _OWNER_SCOPE,
        _OWNER_SCOPE,
    )
    assert {record.memory_id for record in stored_after_mutation} == {
        original_memory_id,
        second_memory_id,
    }
    assert {record.content for record in stored_after_mutation} == {
        _MEMORY_TEXT,
        _SECOND_MEMORY_TEXT,
    }

    healthy_wire = _install_wire(
        monkeypatch,
        reloaded_agent,
        [_chat_sse_text("Both temporary colours are still available.")],
    )
    healthy_turn = await _say(
        hass,
        reloaded_agent,
        _OWNER_ID,
        "Are both temporary colours still available?",
        conversation_id=recall_turn.conversation_id,
    )

    assert _speech(healthy_turn) == "Both temporary colours are still available."
    prompt = _system_prompt(healthy_wire.requests[0]["body"])
    assert _MEMORY_TEXT in prompt
    assert _SECOND_MEMORY_TEXT in prompt

    # Real setup/unload/reload must not assemble or reinstall manager behavior.
    for name, method in owners_before_setup.items():
        assert getattr(TemporaryMemory, name) is method
        assert method.__qualname__ == f"TemporaryMemory.{name}"
        assert not hasattr(method, "__wrapped__")
    from custom_components.extended_openai_conversation_responses import management_ui

    assert (
        management_ui.async_read_temporary_memory_snapshot
        is async_read_temporary_memory_snapshot
    )
    assert (
        await async_read_temporary_memory_snapshot(
            hass,
            entry.entry_id,
            conversation_subentry.subentry_id,
            _OWNER_SCOPE,
            "user:foreign-owner",
        )
        == []
    )
    assert (
        await reloaded_agent._temporary_memory.async_active_snapshot(
            _OWNER_SCOPE,
            "user:foreign-owner",
        )
        == []
    )
