"""Committed retention reaches an active fallback through real conversation turns."""

import pytest

from custom_components.extended_openai_conversation_responses import usage
from homeassistant.components import conversation
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _say, _speech
from tests_real_ha.test_entry_point_contract_matrix import _management


@pytest.mark.parametrize("days", [0, 30])
async def test_recovered_storage_policy_updates_live_volatile_manager(
    hass, monkeypatch, days
):
    native_load = usage._UsageStore.async_load

    async def fail_load(_store):
        raise OSError("transient Usage load failure")

    monkeypatch.setattr(usage._UsageStore, "async_load", fail_load)
    agent = await _agent(
        hass,
        usage_request_retention_days=30 if days == 0 else 0,
        usage_run_retention_days=30 if days == 0 else 0,
    )
    fallback = agent._usage
    assert (
        fallback
        is hass.data[usage._VOLATILE_USAGE_MANAGERS][
            (agent.entry.entry_id, agent.subentry.subentry_id)
        ]
    )
    monkeypatch.setattr(usage._UsageStore, "async_load", native_load)
    _provider(monkeypatch, agent, ["First reply"])
    first = await _say(hass, agent, "First turn")
    assert _speech(first) == "First reply"
    if fallback._prune_task:
        await fallback._prune_task
    command = await _management(hass, agent)
    configuration = await command("get")
    await command(
        "save",
        config={
            **configuration["config"],
            "usage_request_retention_days": days,
            "usage_run_retention_days": days,
        },
        revision=configuration["revision"],
    )
    assert fallback.request_retention_days == fallback.run_retention_days == days
    assert fallback.requests == [] and fallback.runs == []
    durable = await usage.async_get_durable_usage(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    assert durable is not fallback
    await hass.async_block_till_done(wait_background_tasks=True)
    agent = conversation.async_get_agent(hass, agent.entry.entry_id)
    _provider(monkeypatch, agent, ["Second reply"])
    second = await _say(hass, agent, "Next turn", first.conversation_id)
    assert _speech(second) == "Second reply"
    assert agent._usage is fallback
    assert len(fallback.requests) == len(fallback.runs) == (0 if days == 0 else 1)
    assert fallback.totals.api_request_count == 2
    assert await hass.config_entries.async_unload(agent.entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
