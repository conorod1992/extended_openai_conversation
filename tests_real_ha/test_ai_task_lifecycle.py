"""Real-HA lifecycle acceptance for Extended OpenAI AI Task entities."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from homeassistant.components import ai_task
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_ai_task_runtime import FakeClient, _setup_entry


@pytest.mark.asyncio
async def test_ai_task_entity_survives_unload_reload_and_stale_id_fails_cleanly(
    hass: HomeAssistant,
) -> None:
    """The same AI Task registry identity must recover after a real entry reload."""
    entry, entity_id, initial_client = await _setup_entry(hass, ["Before unload"])
    registry = er.async_get(hass)

    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "ai_task_data"
    )
    row_before = registry.async_get(entity_id)
    assert row_before is not None
    assert row_before.domain == ai_task.DOMAIN
    assert row_before.platform == DOMAIN
    assert row_before.config_entry_id == entry.entry_id
    assert row_before.config_subentry_id == subentry.subentry_id
    identity_before = (
        row_before.entity_id,
        row_before.unique_id,
        row_before.config_subentry_id,
    )

    before = await ai_task.async_generate_data(
        hass,
        task_name="Before unload",
        entity_id=entity_id,
        instructions="Return the pre-unload marker.",
    )
    assert before.data == "Before unload"
    assert len(initial_client.completions.calls) == 1

    # Exercise Home Assistant's real config-entry unload path. The entity-registry
    # row is intentionally retained, but the runtime entity must no longer be
    # callable through the public AI Task API while the integration is unloaded.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    row_unloaded = registry.async_get(entity_id)
    assert row_unloaded is not None
    assert (
        row_unloaded.entity_id,
        row_unloaded.unique_id,
        row_unloaded.config_subentry_id,
    ) == identity_before

    with pytest.raises(
        HomeAssistantError,
        match=rf"AI Task entity {entity_id} not found",
    ):
        await ai_task.async_generate_data(
            hass,
            task_name="Stale entity while unloaded",
            entity_id=entity_id,
            instructions="This must not reach a provider.",
        )
    assert len(initial_client.completions.calls) == 1

    # Reload through Home Assistant's real config-entry manager. Replace only the
    # provider SDK seam, matching the existing AI Task runtime acceptance tests.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    reloaded_client = FakeClient(["After reload"])
    entry.runtime_data = reloaded_client

    rows_after = [
        row
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
        if row.domain == ai_task.DOMAIN
    ]
    assert len(rows_after) == 1
    row_after = rows_after[0]
    assert (
        row_after.entity_id,
        row_after.unique_id,
        row_after.config_subentry_id,
    ) == identity_before

    after = await ai_task.async_generate_data(
        hass,
        task_name="After reload",
        entity_id=entity_id,
        instructions="Return the post-reload marker.",
    )
    assert after.data == "After reload"
    assert len(reloaded_client.completions.calls) == 1
    assert "post-reload marker" in str(
        reloaded_client.completions.calls[0]["messages"]
    )

    # The stale pre-unload provider client must never be reused after the reload,
    # and Home Assistant must not create a duplicate AI Task registry entity.
    assert len(initial_client.completions.calls) == 1
