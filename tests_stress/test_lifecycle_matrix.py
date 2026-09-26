"""Nightly lifecycle contract shared by the supported HA version matrix."""

from __future__ import annotations

import random

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.const import (
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from tests_real_ha.test_multi_entry_lifecycle import (
    _assert_agent_can_answer,
    _integration_tasks,
)
from tests_stress.conftest import record
from tests_stress.test_runtime_soak import _resource_footprint


def _entry(number: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Lifecycle provider {number}",
        data={CONF_API_KEY: "sk-nightly-lifecycle", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": f"Provider {number} conversation {subentry}",
                "unique_id": None,
            }
            for subentry in range(2)
        ],
    )


def _rows(hass: HomeAssistant, entry: MockConfigEntry) -> set[tuple[str, str]]:
    return {
        (row.entity_id, row.config_subentry_id)
        for row in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


@pytest.mark.asyncio
async def test_seeded_multi_entry_lifecycle_contract(
    hass: HomeAssistant,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    """Keep two entries and four subentries isolated through 60 to 100 reloads."""
    rng = random.Random(stress_seed ^ 0x1C1EC1E)
    entries = [_entry(index) for index in range(2)]
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    baseline_rows = {entry.entry_id: _rows(hass, entry) for entry in entries}
    assert all(
        len({subentry for _, subentry in rows}) == 2 for rows in baseline_rows.values()
    )
    assert not baseline_rows[entries[0].entry_id] & baseline_rows[entries[1].entry_id]
    markers: dict[tuple[str, str], str] = {}
    for entry in entries:
        for subentry in entry.subentries.values():
            marker = f"private-{entry.entry_id}-{subentry.subentry_id}"
            memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
            assert (
                await memory.async_add(
                    "nightly-owner", marker, "acceptance", "explicit", key="private"
                )
            )["status"] == "created"
            markers[(entry.entry_id, subentry.subentry_id)] = marker

    baseline_resources = _resource_footprint(hass)
    baseline_tasks = _integration_tasks()
    peak_resources = dict(baseline_resources)
    record(
        stress_trace,
        "resource_baseline",
        resources=baseline_resources,
        integration_tasks=len(baseline_tasks),
    )
    assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)

    cycles = 60 if stress_scale == 1 else 100
    for cycle in range(cycles):
        index = rng.randrange(2)
        entry, sibling = entries[index], entries[1 - index]
        sibling_agent = conversation.async_get_agent(hass, sibling.entry_id)
        assert isinstance(sibling_agent, ExtendedOpenAIAgentEntity)
        record(stress_trace, "unload_setup", cycle=cycle, entry=index)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert conversation.async_get_agent(hass, entry.entry_id) is None
        assert conversation.async_get_agent(hass, sibling.entry_id) is sibling_agent
        assert _rows(hass, sibling) == baseline_rows[sibling.entry_id]
        assert hass.services.has_service(DOMAIN, SERVICE_PROCESS)
        if cycle % 10 == 0:
            await _assert_agent_can_answer(
                hass,
                sibling_agent,
                text=f"sibling stays available during cycle {cycle}",
                reply="sibling ready",
            )

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        reloaded_agent = conversation.async_get_agent(hass, entry.entry_id)
        assert isinstance(reloaded_agent, ExtendedOpenAIAgentEntity)
        assert conversation.async_get_agent(hass, sibling.entry_id) is sibling_agent
        if cycle % 10 == 0:
            await _assert_agent_can_answer(
                hass,
                reloaded_agent,
                text=f"reloaded entry works in cycle {cycle}",
                reply="reloaded ready",
            )
        assert all(
            _rows(hass, item) == baseline_rows[item.entry_id] for item in entries
        )
        current_resources = _resource_footprint(hass)
        for key, value in current_resources.items():
            peak_resources[key] = max(peak_resources[key], value)
        if cycle % 10 == 0:
            record(
                stress_trace,
                "resource_checkpoint",
                cycle=cycle,
                resources=current_resources,
            )
        assert current_resources == baseline_resources
        assert _integration_tasks() == baseline_tasks
        for item in entries:
            for subentry in item.subentries.values():
                memory = await async_get_memory(
                    hass, item.entry_id, subentry.subentry_id
                )
                records = await memory.async_list("nightly-owner", limit=10)
                assert [record.content for record in records] == [
                    markers[(item.entry_id, subentry.subentry_id)]
                ]

    for entry in entries:
        assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # EOAI registers the shared process service once per HA instance. It remains
    # registered after the last entry unloads, but its count must not grow.
    assert (
        _resource_footprint(hass)["registered_services"]
        == baseline_resources["registered_services"]
    )
    assert not (_integration_tasks() - baseline_tasks)
    record(
        stress_trace,
        "summary",
        cycles=cycles,
        entries=2,
        subentries=4,
        baseline_resources=baseline_resources,
        peak_resources=peak_resources,
        after_resources=_resource_footprint(hass),
        baseline_tasks=len(baseline_tasks),
        after_tasks=len(_integration_tasks()),
    )
