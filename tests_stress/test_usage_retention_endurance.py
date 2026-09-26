"""Nightly public-turn accounting while Usage retention and time move."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    usage as usage_module,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from tests_real_ha.test_corrupt_subsystem_store_startup_isolation import (
    _real_store_io,  # noqa: F401 - register the genuine Store fixture for this module
)
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire
from tests_stress.conftest import record


async def _say(hass: HomeAssistant, agent: Any, user_id: str, index: int) -> Any:
    return await conversation.async_converse(
        hass=hass,
        text=f"Account for {agent.entry.title} request {index}",
        conversation_id=None,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.asyncio
async def test_concurrent_usage_writes_survive_retention_jumps_and_reload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    _real_store_io: None,  # noqa: F811 - the imported fixture name is intentional
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    """Two agents and users retain exact totals as detail windows churn."""
    users = [
        MockUser(id=f"usage-endurance-{stress_seed}-{index}", name=f"Usage {index}")
        for index in range(2)
    ]
    for user in users:
        user.add_to_hass(hass)
    agents = [
        await _agent(hass, title=f"Usage Endurance {index}") for index in range(2)
    ]
    assert agents[0].entry.entry_id != agents[1].entry.entry_id
    assert all(agent is not None and agent._usage is not None for agent in agents)
    turns_per_agent = 10 if stress_scale == 1 else 32
    wire = _install_wire(
        monkeypatch,
        agents[0],
        [_chat_sse_text("Accounted.") for _ in range(2 * (turns_per_agent + 2))],
    )

    # Independent public turns may finish in any order, but each agent owns its
    # accounting and each provider request must appear exactly once.
    for start in range(0, turns_per_agent, 4):
        tasks = [
            _say(hass, agents[agent_index], users[agent_index].id, index)
            for agent_index in range(2)
            for index in range(start, min(start + 4, turns_per_agent))
        ]
        results = await asyncio.gather(*tasks)
        assert all(result.response.error_code is None for result in results), [
            result.response.error_code for result in results
        ]
    await hass.async_block_till_done()
    base = dt_util.utcnow()
    managers = [agent._usage for agent in agents]
    assert len(wire.requests) == 2 * turns_per_agent
    for agent, manager in zip(agents, managers, strict=True):
        assert manager.totals.conversation_count == turns_per_agent
        assert manager.totals.api_request_count == turns_per_agent
        assert manager.totals.successful_request_count == turns_per_agent
        assert manager.totals.failed_request_count == 0
        assert len(manager.requests) == turns_per_agent
        assert len(manager.runs) == turns_per_agent
        assert (
            sum(
                agent.entry.title in str(request["body"]["messages"])
                for request in wire.requests
            )
            == turns_per_agent
        )
        assert {item.agent_subentry_id for item in manager.requests} == {
            agent.subentry.subentry_id
        }
        for item in manager.requests[: turns_per_agent // 2]:
            item.timestamp = (base - timedelta(days=45)).isoformat()
        for item in manager.runs[: turns_per_agent // 2]:
            item.started_at = (base - timedelta(days=45)).isoformat()
        manager.request_retention_days = 30
        manager.run_retention_days = 30

    # Prune and new public writes overlap. The lock must preserve new detail rows
    # while removing only the artificially aged half of each agent's history.
    await asyncio.gather(
        *(manager.async_prune_details() for manager in managers),
        *(
            _say(hass, agents[index], users[index].id, turns_per_agent)
            for index in range(2)
        ),
    )
    await hass.async_block_till_done()
    for manager in managers:
        assert manager.totals.conversation_count == turns_per_agent + 1
        assert manager.totals.api_request_count == turns_per_agent + 1
        assert len(manager.requests) == turns_per_agent // 2 + 1
        assert len(manager.runs) == turns_per_agent // 2 + 1
        manager.request_retention_days = 10
        manager.run_retention_days = 10

    with monkeypatch.context() as clock:
        clock.setattr(usage_module.dt_util, "utcnow", lambda: base + timedelta(days=5))
        await asyncio.gather(*(manager.async_prune_details() for manager in managers))
    with monkeypatch.context() as clock:
        clock.setattr(usage_module.dt_util, "utcnow", lambda: base - timedelta(days=5))
        await asyncio.gather(*(manager.async_prune_details() for manager in managers))
    assert all(
        len(manager.requests) == turns_per_agent // 2 + 1 for manager in managers
    )

    with monkeypatch.context() as clock:
        clock.setattr(usage_module.dt_util, "utcnow", lambda: base + timedelta(days=45))
        await asyncio.gather(*(manager.async_prune_details() for manager in managers))
    assert all(not manager.requests and not manager.runs for manager in managers)

    results = await asyncio.gather(
        *(
            _say(hass, agents[index], users[index].id, turns_per_agent + 1)
            for index in range(2)
        )
    )
    assert all(result.response.error_code is None for result in results)
    await hass.async_block_till_done()
    for agent, manager in zip(agents, managers, strict=True):
        assert manager.totals.conversation_count == turns_per_agent + 2
        assert manager.totals.api_request_count == turns_per_agent + 2
        assert manager.totals.successful_request_count == turns_per_agent + 2
        assert manager.totals.failed_request_count == 0
        assert len(manager.requests) == len(manager.runs) == 1
        await manager._async_save_details()
        restarted = UsageManager(
            manager._storage,
            manager._daily_storage,
            manager._detail_storage,
            agent_subentry_id=agent.subentry.subentry_id,
            request_retention_days=10,
            run_retention_days=10,
        )
        await restarted.async_initialize()
        assert restarted.totals.conversation_count == turns_per_agent + 2
        assert len(restarted.requests) == len(restarted.runs) == 1
        assert restarted.requests[0].agent_subentry_id == agent.subentry.subentry_id
    assert agents[0].subentry.subentry_id != agents[1].subentry.subentry_id
    record(
        stress_trace,
        "summary",
        layer="Real HA Assist and Store",
        public_turns=2 * (turns_per_agent + 2),
        usage_retention_prunes=8,
        usage_scopes=2,
        forward_backward_clock_changes=3,
    )
