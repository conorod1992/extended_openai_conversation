"""Real-HA acceptance for overlapping lifecycle operations across two entries."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry

_WAIT_TIMEOUT = 10


def _conversation_row(hass: HomeAssistant, entry: MockConfigEntry):
    rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return next(row for row in rows if row.domain == "conversation")


def _agent(hass: HomeAssistant, entry: MockConfigEntry):
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    return agent


async def _process(hass: HomeAssistant, entity_id: str) -> dict:
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_PROCESS,
        {"text": "what time is it", "agent_id": entity_id},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    return response


@pytest.mark.asyncio
async def test_two_entries_survive_overlapping_reload_and_unload_lifecycle(
    hass: HomeAssistant,
) -> None:
    """Concurrent lifecycle work on one entry must not corrupt its sibling."""
    first = _make_entry(
        "Concurrent First",
        include_ai_task=False,
        local_intents=True,
    )
    second = _make_entry(
        "Concurrent Second",
        include_ai_task=False,
        local_intents=True,
    )
    await _setup_entry(hass, first)
    await _setup_entry(hass, second)

    first_row_before = _conversation_row(hass, first)
    second_row_before = _conversation_row(hass, second)
    first_agent_before = _agent(hass, first)
    second_agent_before = _agent(hass, second)

    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
    assert (await _process(hass, first_row_before.entity_id))["handled_locally"] is True
    assert (await _process(hass, second_row_before.entity_id))["handled_locally"] is True

    # Start both reloads in the same event-loop turn. Home Assistant may serialize
    # portions internally, but both entry lifecycle state machines are active from
    # the caller's perspective and must remain isolated from each other.
    first_reload, second_reload = await asyncio.wait_for(
        asyncio.gather(
            hass.config_entries.async_reload(first.entry_id),
            hass.config_entries.async_reload(second.entry_id),
        ),
        timeout=_WAIT_TIMEOUT,
    )
    assert first_reload is True
    assert second_reload is True
    await hass.async_block_till_done()

    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED
    first_agent_after_reload = _agent(hass, first)
    second_agent_after_reload = _agent(hass, second)
    assert first_agent_after_reload is not first_agent_before
    assert second_agent_after_reload is not second_agent_before

    first_row_after_reload = _conversation_row(hass, first)
    second_row_after_reload = _conversation_row(hass, second)
    assert (
        first_row_after_reload.entity_id,
        first_row_after_reload.unique_id,
        first_row_after_reload.config_subentry_id,
    ) == (
        first_row_before.entity_id,
        first_row_before.unique_id,
        first_row_before.config_subentry_id,
    )
    assert (
        second_row_after_reload.entity_id,
        second_row_after_reload.unique_id,
        second_row_after_reload.config_subentry_id,
    ) == (
        second_row_before.entity_id,
        second_row_before.unique_id,
        second_row_before.config_subentry_id,
    )

    assert (await _process(hass, first_row_after_reload.entity_id))["handled_locally"] is True
    assert (await _process(hass, second_row_after_reload.entity_id))["handled_locally"] is True

    # Overlap a real unload of the first entry with a second reload of its sibling.
    # The sibling must emerge healthy while the unloaded entry remains absent from
    # the live conversation-agent registry.
    first_unload, second_reload_again = await asyncio.wait_for(
        asyncio.gather(
            hass.config_entries.async_unload(first.entry_id),
            hass.config_entries.async_reload(second.entry_id),
        ),
        timeout=_WAIT_TIMEOUT,
    )
    assert first_unload is True
    assert second_reload_again is True
    await hass.async_block_till_done()

    assert first.state is ConfigEntryState.NOT_LOADED
    assert second.state is ConfigEntryState.LOADED
    assert conversation.async_get_agent(hass, first.entry_id) is None
    second_agent_final = _agent(hass, second)
    assert second_agent_final is not second_agent_after_reload
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    second_row_final = _conversation_row(hass, second)
    assert (
        second_row_final.entity_id,
        second_row_final.unique_id,
        second_row_final.config_subentry_id,
    ) == (
        second_row_before.entity_id,
        second_row_before.unique_id,
        second_row_before.config_subentry_id,
    )
    assert (await _process(hass, second_row_final.entity_id))["handled_locally"] is True

    with pytest.raises(
        HomeAssistantError,
        match="Extended OpenAI conversation agent not found",
    ):
        await _process(hass, first_row_before.entity_id)

    # Finally bring the unloaded entry back while the sibling stays live. This
    # catches stale shared ownership/registry state left behind by the overlap.
    assert await asyncio.wait_for(
        hass.config_entries.async_setup(first.entry_id),
        timeout=_WAIT_TIMEOUT,
    )
    await hass.async_block_till_done()
    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED

    first_row_final = _conversation_row(hass, first)
    assert (
        first_row_final.entity_id,
        first_row_final.unique_id,
        first_row_final.config_subentry_id,
    ) == (
        first_row_before.entity_id,
        first_row_before.unique_id,
        first_row_before.config_subentry_id,
    )

    first_rows = [
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), first.entry_id)
        if row.domain == "conversation"
    ]
    second_rows = [
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), second.entry_id)
        if row.domain == "conversation"
    ]
    assert len(first_rows) == 1
    assert len(second_rows) == 1
    assert (await _process(hass, first_row_final.entity_id))["handled_locally"] is True
    assert (await _process(hass, second_row_final.entity_id))["handled_locally"] is True
