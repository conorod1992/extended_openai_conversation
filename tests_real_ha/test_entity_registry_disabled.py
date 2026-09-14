"""Real-HA acceptance for entity-registry-disabled conversation agents."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests_real_ha.test_acceptance_lifecycle import (
    _conversation_subentry,
    _make_entry,
    _registry_entries,
    _setup_entry,
)


def _conversation_row(hass: HomeAssistant, entry: MockConfigEntry):
    """Return the sole conversation entity-registry row for one entry."""
    rows = [row for row in _registry_entries(hass, entry) if row.domain == "conversation"]
    assert len(rows) == 1
    return rows[0]


def _guest_mode_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """Return the sibling guest-mode sensor entity id."""
    conversation_id = _conversation_subentry(entry).subentry_id
    return next(
        row.entity_id
        for row in _registry_entries(hass, entry)
        if row.unique_id == f"{conversation_id}_guest_mode"
    )


async def _process(hass: HomeAssistant, agent_id: str) -> dict:
    """Call the integration's real shared process service."""
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_PROCESS,
        {
            "text": "what time is it",
            "agent_id": agent_id,
        },
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    return response


@pytest.mark.asyncio
async def test_registry_disabled_conversation_is_not_routable_and_recovers_cleanly(
    hass: HomeAssistant,
) -> None:
    """Disabling only the conversation entity suppresses routing without disabling its entry."""
    entry = _make_entry(
        "Registry Disabled",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, entry)

    registry = er.async_get(hass)
    row_before = _conversation_row(hass, entry)
    entity_id = row_before.entity_id
    unique_id = row_before.unique_id
    subentry_id = row_before.config_subentry_id
    guest_mode_entity_id = _guest_mode_entity_id(hass, entry)

    original_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert original_agent is not None
    assert conversation.async_get_agent(hass, entity_id) is original_agent

    initial = await _process(hass, entity_id)
    assert initial["handled_locally"] is True
    assert initial["response"]

    guest_state = hass.states.get(guest_mode_entity_id)
    assert guest_state is not None
    assert guest_state.state != STATE_UNAVAILABLE

    disabled = registry.async_update_entity(
        entity_id,
        disabled_by=RegistryEntryDisabler.USER,
    )
    assert disabled.disabled_by is RegistryEntryDisabler.USER

    # Reload through Home Assistant so the entity platform consumes the registry
    # disabled state exactly as it would after a user disables the entity in UI.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    disabled_row = _conversation_row(hass, entry)
    assert disabled_row.entity_id == entity_id
    assert disabled_row.unique_id == unique_id
    assert disabled_row.config_subentry_id == subentry_id
    assert disabled_row.disabled_by is RegistryEntryDisabler.USER

    # The parent config entry and sibling sensor platform remain healthy, but HA must
    # not expose a disabled conversation entity as a live Assist agent.
    assert conversation.async_get_agent(hass, entry.entry_id) is None
    assert conversation.async_get_agent(hass, entity_id) is None
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    guest_state_while_disabled = hass.states.get(guest_mode_entity_id)
    assert guest_state_while_disabled is not None
    assert guest_state_while_disabled.state != STATE_UNAVAILABLE

    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI conversation agent not found",
    ):
        await _process(hass, entity_id)

    with pytest.raises(
        HomeAssistantError,
        match="No Extended OpenAI conversation agent is available",
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PROCESS,
            {"text": "what time is it"},
            blocking=True,
            return_response=True,
        )

    enabled = registry.async_update_entity(entity_id, disabled_by=None)
    assert enabled.disabled_by is None

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    row_after = _conversation_row(hass, entry)
    assert row_after.entity_id == entity_id
    assert row_after.unique_id == unique_id
    assert row_after.config_subentry_id == subentry_id
    assert row_after.disabled_by is None

    replacement_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert replacement_agent is not None
    assert replacement_agent is not original_agent
    assert conversation.async_get_agent(hass, entity_id) is replacement_agent

    # Registry enablement must recreate exactly one callable agent under the same
    # identity rather than generating a duplicate or requiring subentry recreation.
    conversation_rows = [
        row for row in _registry_entries(hass, entry) if row.domain == "conversation"
    ]
    assert len(conversation_rows) == 1

    recovered = await _process(hass, entity_id)
    assert recovered["handled_locally"] is True
    assert recovered["response"]

    guest_state_after = hass.states.get(guest_mode_entity_id)
    assert guest_state_after is not None
    assert guest_state_after.state != STATE_UNAVAILABLE
