"""Nightly genuine Store recovery from structurally invalid data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.components import conversation
from homeassistant.core import HomeAssistant
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS, CONF_API_MODE, CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED, CONF_TEMPORARY_MEMORY,
    SUBSYSTEM_STATUS_KEY, TEMPORARY_MEMORY_BALANCED,
)
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_corrupt_subsystem_store_startup_isolation import (
    _real_store_io, _purge_cached_managers,
)
from tests_stress.conftest import record


@pytest.mark.no_fail_on_log_exception
@pytest.mark.asyncio
async def test_invalid_store_structure_stays_durable_until_repaired(
    hass: HomeAssistant,
    _real_store_io: None,
    stress_trace: list[dict],
) -> None:
    """A valid JSON envelope with invalid data degrades only its owner."""
    entry = _make_entry(
        "Invalid knowledge structure",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._knowledge is not None
    assert agent._temporary_memory is not None
    await agent._knowledge.async_create(
        "Durable source", "Reference", "Important durable knowledge"
    )
    path = Path(agent._knowledge._storage._store.path)
    original = await hass.async_add_executor_job(path.read_text, "utf-8")
    payload = json.loads(original)
    payload["data"]["sources"] = {"invalid": "mapping-instead-of-list"}
    damaged = json.dumps(payload)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    _purge_cached_managers(hass.data)
    await hass.async_add_executor_job(path.write_text, damaged, "utf-8")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    degraded = conversation.async_get_agent(hass, entry.entry_id)
    assert degraded is not None
    assert degraded._knowledge is None
    assert degraded._temporary_memory is not None
    assert await hass.async_add_executor_job(path.read_text, "utf-8") == damaged
    subentry_id = degraded.subentry.subentry_id
    status = hass.data[SUBSYSTEM_STATUS_KEY][(entry.entry_id, subentry_id)]
    assert status["knowledge"]["status"] != "healthy"
    assert status["temporary_memory"]["status"] == "healthy"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    _purge_cached_managers(hass.data)
    await hass.async_add_executor_job(path.write_text, original, "utf-8")
    assert await hass.config_entries.async_setup(entry.entry_id)
    restored = conversation.async_get_agent(hass, entry.entry_id)
    assert restored is not None
    assert restored._knowledge is not None
    assert restored._knowledge.source_count == 1
    record(
        stress_trace, "summary", layer="Real HA",
        invalid_store_structure_recoveries=1,
    )


