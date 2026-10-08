"""Expiry must work in idle HA and while new archive collection is disabled."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from homeassistant.util import dt as dt_util
from custom_components.extended_openai_conversation_responses.usage import (
    async_get_usage,
    RequestUsage,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from homeassistant.core import Context
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider
from tests_real_ha.test_entry_point_contract_matrix import _management
from homeassistant.components import conversation


@pytest.mark.parametrize("retention", [1, 7, 30])
async def test_usage_expires_without_another_run(hass, freezer, retention):
    await hass.async_start()
    usage = await async_get_usage(hass, "idle-entry", "idle-agent")
    usage.request_retention_days = usage.run_retention_days = retention
    now = dt_util.utcnow()
    async with usage.async_run():
        await usage.async_record_request(
            successful=True, usage=RequestUsage(total_tokens=123)
        )
    await hass.async_block_till_done(wait_background_tasks=True)
    if usage._prune_task is not None:
        await usage._prune_task
    assert usage.runs and usage.requests
    freezer.move_to(now + timedelta(days=retention + 1, seconds=1))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    if usage._prune_task is not None:
        await usage._prune_task
    assert usage.runs == []
    assert usage.requests == []
    assert usage.as_dict()["total_tokens"] == 123
    await usage.async_shutdown()


async def test_archive_disable_idle_expiry_and_reload(hass, monkeypatch, freezer):
    agent = await _agent(
        hass,
        archive_enabled=True,
        archive_retention_days=7,
        archive_model_search_enabled=False,
    )
    owner = await hass.auth.async_create_user(
        "Archive owner", group_ids=["system-admin"]
    )
    _provider(monkeypatch, agent, ["Reply"])
    now = dt_util.utcnow()
    await conversation.async_converse(
        hass=hass,
        text="Retained content",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=agent.entity_id,
    )
    archive = agent._archive
    assert archive.stats()["turn_count"] == 1
    command = await _management(hass, agent)
    configuration = await command("get")
    await command(
        "save",
        config={**configuration["config"], "archive_enabled": False},
        revision=configuration["revision"],
    )
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert agent._archive is None
    await hass.async_start()
    freezer.move_to(now + timedelta(days=8, seconds=1))
    async_fire_time_changed(hass, fire_all=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert archive.stats()["turn_count"] == 0
    assert agent._archive is None
    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    loaded = await async_get_archive(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    assert loaded.stats()["turn_count"] == 0
