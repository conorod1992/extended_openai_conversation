"""Real-HA acceptance for live config-subentry removal and recreation."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_AI_TASK_OPTIONS,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENTS_ENABLED,
)
from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests_real_ha.test_ai_task_runtime import FakeClient


def _subentry(subentry_type: str, title: str, data: dict | None = None) -> dict:
    """Return storage-shaped subentry data for MockConfigEntry."""
    return {
        "data": data or {},
        "subentry_type": subentry_type,
        "title": title,
        "unique_id": None,
    }


def _entry() -> MockConfigEntry:
    """Build one entry with two conversations and one AI Task subentry."""
    conversation_data = {CONF_LOCAL_INTENTS_ENABLED: True}
    ai_task_data = dict(DEFAULT_AI_TASK_OPTIONS)
    ai_task_data[CONF_API_MODE] = API_MODE_CHAT_COMPLETIONS
    return MockConfigEntry(
        domain=DOMAIN,
        title="Live Subentry Removal",
        data={
            CONF_API_KEY: "sk-live-subentry-test",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("conversation", "Conversation A", dict(conversation_data)),
            _subentry("conversation", "Conversation B", dict(conversation_data)),
            _subentry("ai_task_data", "AI Task", ai_task_data),
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _rows(hass: HomeAssistant, entry: MockConfigEntry):
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def _subentry_by_title(entry: MockConfigEntry, title: str):
    return next(item for item in entry.subentries.values() if item.title == title)


@pytest.mark.asyncio
async def test_live_conversation_subentry_removal_is_isolated_and_recreation_is_clean(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing one live subentry must not damage siblings or future recreation."""
    entry = _entry()
    await _setup_entry(hass, entry)

    removed = _subentry_by_title(entry, "Conversation A")
    remaining = _subentry_by_title(entry, "Conversation B")
    ai_subentry = _subentry_by_title(entry, "AI Task")

    rows_before = _rows(hass, entry)
    removed_rows = [
        row for row in rows_before if row.config_subentry_id == removed.subentry_id
    ]
    remaining_rows = [
        row for row in rows_before if row.config_subentry_id == remaining.subentry_id
    ]
    ai_rows = [
        row
        for row in rows_before
        if row.config_subentry_id == ai_subentry.subentry_id and row.domain == "ai_task"
    ]
    assert removed_rows
    assert any(row.domain == "conversation" for row in removed_rows)
    assert remaining_rows
    assert len(ai_rows) == 1

    removed_entity_ids = {row.entity_id for row in removed_rows}
    remaining_conversation_row = next(
        row for row in remaining_rows if row.domain == "conversation"
    )
    ai_entity_id = ai_rows[0].entity_id

    assert hass.config_entries.async_remove_subentry(entry, removed.subentry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert removed.subentry_id not in entry.subentries
    assert remaining.subentry_id in entry.subentries
    assert ai_subentry.subentry_id in entry.subentries

    rows_after_removal = _rows(hass, entry)
    assert not any(
        row.config_subentry_id == removed.subentry_id for row in rows_after_removal
    )
    assert removed_entity_ids.isdisjoint(
        {row.entity_id for row in rows_after_removal}
    )

    remaining_conversation_rows = [
        row
        for row in rows_after_removal
        if row.config_subentry_id == remaining.subentry_id
        and row.domain == "conversation"
    ]
    assert len(remaining_conversation_rows) == 1
    assert remaining_conversation_rows[0].entity_id == remaining_conversation_row.entity_id

    remaining_agent = conversation.async_get_agent(
        hass, remaining_conversation_rows[0].entity_id
    )
    assert remaining_agent is not None
    provider_path = AsyncMock(
        side_effect=AssertionError(
            "Remaining local-intent conversation unexpectedly reached the provider"
        )
    )
    monkeypatch.setattr(
        remaining_agent, "_async_handle_message_with_ha_tools", provider_path
    )
    result = await conversation.async_converse(
        hass=hass,
        text="what time is it",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=remaining_conversation_rows[0].entity_id,
    )
    provider_path.assert_not_awaited()
    assert result.response.as_dict()["speech"]["plain"]["speech"]

    ai_rows_after = [
        row
        for row in rows_after_removal
        if row.config_subentry_id == ai_subentry.subentry_id and row.domain == "ai_task"
    ]
    assert len(ai_rows_after) == 1
    assert ai_rows_after[0].entity_id == ai_entity_id

    # Exercise the surviving AI Task through Home Assistant's public API. Replacing
    # only the provider boundary keeps this deterministic while proving the entity
    # created by the sibling subentry remains genuinely callable after the reload.
    client = FakeClient(["AI Task survived sibling removal"])
    entry.runtime_data = client
    task_result = await ai_task.async_generate_data(
        hass,
        task_name="Sibling survival",
        entity_id=ai_entity_id,
        instructions="Return a short health confirmation.",
    )
    assert task_result.data == "AI Task survived sibling removal"
    assert len(client.completions.calls) == 1

    recreated = ConfigSubentry(
        data=MappingProxyType({CONF_LOCAL_INTENTS_ENABLED: True}),
        subentry_type="conversation",
        title="Conversation A Recreated",
        unique_id=None,
    )
    assert hass.config_entries.async_add_subentry(entry, recreated)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert recreated.subentry_id in entry.subentries
    recreated_rows = [
        row
        for row in _rows(hass, entry)
        if row.config_subentry_id == recreated.subentry_id
    ]
    assert len([row for row in recreated_rows if row.domain == "conversation"]) == 1

    # Recreating the logical agent must not resurrect any removed registry identity
    # or duplicate the untouched sibling entities.
    final_rows = _rows(hass, entry)
    assert removed_entity_ids.isdisjoint({row.entity_id for row in final_rows})
    assert len(
        [
            row
            for row in final_rows
            if row.config_subentry_id == remaining.subentry_id
            and row.domain == "conversation"
        ]
    ) == 1
    assert len(
        [
            row
            for row in final_rows
            if row.config_subentry_id == ai_subentry.subentry_id
            and row.domain == "ai_task"
        ]
    ) == 1
