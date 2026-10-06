"""Endurance coverage for repeated live config-subentry churn."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    DEFAULT_AI_TASK_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.local_intents import (
    CONF_LOCAL_INTENTS_ENABLED,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_live_subentry_removal import (
    _entry,
    _setup_entry,
    _subentry_by_title,
)


@pytest.mark.asyncio
async def test_repeated_conversation_and_ai_task_churn_leaves_no_registry_accumulation(
    hass,
    stress_scale: int,
) -> None:
    """Repeated generations must not accumulate stale entities or damage siblings."""
    entry = _entry()
    await _setup_entry(hass, entry)

    survivor = _subentry_by_title(entry, "Conversation B")
    survivor_row = next(
        row
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if row.config_subentry_id == survivor.subentry_id
        and row.domain == conversation.DOMAIN
    )
    survivor_entity_id = survivor_row.entity_id

    iterations = 20 if stress_scale == 1 else 60
    retired_subentry_ids: set[str] = set()
    retired_entity_ids: set[str] = set()

    for index in range(iterations):
        conversation_subentry = ConfigSubentry(
            data=MappingProxyType({CONF_LOCAL_INTENTS_ENABLED: True}),
            subentry_type="conversation",
            title=f"Churn Conversation {index}",
            unique_id=None,
        )
        ai_options = dict(DEFAULT_AI_TASK_OPTIONS)
        ai_options[CONF_API_MODE] = API_MODE_CHAT_COMPLETIONS
        ai_subentry = ConfigSubentry(
            data=MappingProxyType(ai_options),
            subentry_type="ai_task_data",
            title=f"Churn AI Task {index}",
            unique_id=None,
        )

        assert hass.config_entries.async_add_subentry(entry, conversation_subentry)
        await hass.async_block_till_done()
        assert hass.config_entries.async_add_subentry(entry, ai_subentry)
        await hass.async_block_till_done()

        rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        conversation_rows = [
            row
            for row in rows
            if row.config_subentry_id == conversation_subentry.subentry_id
        ]
        ai_rows = [
            row
            for row in rows
            if row.config_subentry_id == ai_subentry.subentry_id
        ]
        assert len([row for row in conversation_rows if row.domain == "conversation"]) == 1
        assert len([row for row in ai_rows if row.domain == "ai_task"]) == 1

        retired_subentry_ids.update(
            (conversation_subentry.subentry_id, ai_subentry.subentry_id)
        )
        retired_entity_ids.update(
            row.entity_id for row in (*conversation_rows, *ai_rows)
        )

        assert hass.config_entries.async_remove_subentry(
            entry, conversation_subentry.subentry_id
        )
        await hass.async_block_till_done()
        assert hass.config_entries.async_remove_subentry(
            entry, ai_subentry.subentry_id
        )
        await hass.async_block_till_done()

        remaining = er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        assert not any(
            row.config_subentry_id
            in {conversation_subentry.subentry_id, ai_subentry.subentry_id}
            for row in remaining
        )
        assert retired_entity_ids.isdisjoint({row.entity_id for row in remaining})

        survivor_rows = [
            row
            for row in remaining
            if row.config_subentry_id == survivor.subentry_id
            and row.domain == conversation.DOMAIN
        ]
        assert len(survivor_rows) == 1
        assert survivor_rows[0].entity_id == survivor_entity_id
        assert conversation.async_get_agent(hass, survivor_entity_id) is not None

    # A reload after all churn must reconstruct only current generations.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    final_rows = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert not any(
        row.config_subentry_id in retired_subentry_ids for row in final_rows
    )
    assert retired_entity_ids.isdisjoint({row.entity_id for row in final_rows})
    assert len(
        [
            row
            for row in final_rows
            if row.config_subentry_id == survivor.subentry_id
            and row.domain == conversation.DOMAIN
        ]
    ) == 1
    assert conversation.async_get_agent(hass, survivor_entity_id) is not None
