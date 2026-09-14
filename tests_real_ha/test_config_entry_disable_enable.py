"""Real-HA acceptance for disabling and re-enabling the integration entry."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _setup_entry,
)


def _rows(hass: HomeAssistant, entry_id: str):
    """Return the registry rows owned by one config entry."""
    return er.async_entries_for_config_entry(er.async_get(hass), entry_id)


async def _local_turn(
    hass: HomeAssistant,
    entry_id: str,
) -> conversation.ConversationResult:
    """Run a provider-free public Conversation request."""
    return await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=entry_id,
    )


@pytest.mark.asyncio
async def test_user_disable_enable_preserves_registry_and_restores_runtime(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user-disabled entry tears down cleanly and returns with the same entities."""
    entry = _make_entry(
        "Disable Enable",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, entry)

    old_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(old_agent, ExtendedOpenAIAgentEntity)
    provider_path = AsyncMock(
        side_effect=AssertionError("local intent unexpectedly reached the provider")
    )
    monkeypatch.setattr(old_agent, "_async_handle_message_with_ha_tools", provider_path)

    before = _rows(hass, entry.entry_id)
    before_identity = {
        row.unique_id: (row.entity_id, row.config_subentry_id) for row in before
    }
    conversation_id = _conversation_subentry(entry).subentry_id
    guest_mode_entity_id = next(
        row.entity_id
        for row in before
        if row.unique_id == f"{conversation_id}_guest_mode"
    )

    first = await _local_turn(hass, entry.entry_id)
    assert first.response.error_code is None
    provider_path.assert_not_awaited()

    assert await hass.config_entries.async_set_disabled_by(
        entry.entry_id,
        ConfigEntryDisabler.USER,
    )
    await hass.async_block_till_done()

    assert entry.disabled_by is ConfigEntryDisabler.USER
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is None

    # HA intentionally keeps registry-backed entities represented while their
    # integration is disabled. They must not be recreated under fresh identities.
    disabled_rows = _rows(hass, entry.entry_id)
    assert {
        row.unique_id: (row.entity_id, row.config_subentry_id) for row in disabled_rows
    } == before_identity
    disabled_guest = hass.states.get(guest_mode_entity_id)
    assert disabled_guest is not None
    assert disabled_guest.state == STATE_UNAVAILABLE

    assert await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    await hass.async_block_till_done()

    assert entry.disabled_by is None
    assert entry.state is ConfigEntryState.LOADED
    new_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert isinstance(new_agent, ExtendedOpenAIAgentEntity)
    assert new_agent is not old_agent

    after = _rows(hass, entry.entry_id)
    after_identity = {
        row.unique_id: (row.entity_id, row.config_subentry_id) for row in after
    }
    assert after_identity == before_identity
    assert len(after) == len(before)

    restored_guest = hass.states.get(guest_mode_entity_id)
    assert restored_guest is not None
    assert restored_guest.state != STATE_UNAVAILABLE

    fresh_provider_path = AsyncMock(
        side_effect=AssertionError("local intent unexpectedly reached the provider")
    )
    monkeypatch.setattr(
        new_agent,
        "_async_handle_message_with_ha_tools",
        fresh_provider_path,
    )
    second = await _local_turn(hass, entry.entry_id)
    assert second.response.error_code is None
    fresh_provider_path.assert_not_awaited()

    assert hass.services.has_service(DOMAIN, "process")
