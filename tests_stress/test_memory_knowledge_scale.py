"""Durable user isolation and Knowledge scale across a real HA reload."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from tests_stress.conftest import record


@pytest.mark.asyncio
async def test_scaled_private_records_survive_reload_without_cross_user_reads(
    hass: HomeAssistant,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Large private state",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Large private agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    users = 10 * stress_scale
    per_user = 20
    sources = min(60 * stress_scale, 480)
    for user in range(users):
        for number in range(per_user):
            result = await memory.async_add(
                f"stress-user-{user}",
                f"marker-{user}-record-{number} café 東京",
                "acceptance",
                "explicit",
                key=f"stress-{user}-{number}",
            )
            assert result["status"] == "created"
    record(stress_trace, "memory_population", users=users, records=users * per_user)
    for number in range(sources):
        await knowledge.async_create(
            f"Reference {number % 7}",
            f"description {number}",
            f'Unique source {number} 🎯\n```json\n{{"value": {number}}}\n```',
            enabled=number % 5 != 0,
        )
    record(stress_trace, "knowledge_population", sources=sources)

    for user in range(users):
        records = await memory.async_list(f"stress-user-{user}", limit=50)
        assert len(records) == per_user
        assert all(
            item.user_id == f"stress-user-{user}" and f"marker-{user}-" in item.content
            for item in records
        )
    assert len(await knowledge.async_list()) == sources
    before_memory = await memory.async_backup_data()
    before_knowledge = await knowledge.async_backup_data()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    assert await memory.async_backup_data() == before_memory
    assert await knowledge.async_backup_data() == before_knowledge
    for user in range(users):
        records = await memory.async_list(f"stress-user-{user}", limit=50)
        assert len(records) == per_user
        assert all(item.user_id == f"stress-user-{user}" for item in records)
    assert len(await knowledge.async_list()) == sources
    record(
        stress_trace,
        "summary",
        users=users,
        memory_records=users * per_user,
        knowledge_sources=sources,
        reloads=1,
    )
