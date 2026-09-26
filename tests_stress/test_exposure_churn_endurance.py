"""Seeded HA registry and Assist exposure churn at the provider wire."""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_stress.conftest import record


async def _say(hass: HomeAssistant, agent: Any, user_id: str, round_id: int) -> Any:
    return await conversation.async_converse(
        hass=hass,
        text=f"Describe the available home entities at round {round_id}",
        conversation_id=None,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _contains_id(body: str, entity_id: str) -> bool:
    return re.search(re.escape(entity_id) + r"(?![A-Za-z0-9_])", body) is not None


@pytest.mark.asyncio
async def test_seeded_exposure_registry_churn_never_leaks_removed_targets(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    """Later provider requests see current HA state across two agents and users."""
    rng = random.Random(stress_seed ^ 0xE7A05E)
    registry = er.async_get(hass)
    users = [
        MockUser(id=f"exposure-churn-user-{stress_seed}-{index}", is_owner=True)
        for index in range(3)
    ]
    for user in users:
        user.add_to_hass(hass)
    agents = [await _agent(hass, title=f"Exposure Churn {index}") for index in range(2)]
    assert agents[0].entry.entry_id != agents[1].entry.entry_id
    rounds = 12 if stress_scale == 1 else 36
    wires = [
        _install_wire(
            monkeypatch, agent, [_chat_sse_text("Current HA view received.")] * rounds
        )
        for agent in agents
    ]
    entities: list[dict[str, Any]] = []
    historical_ids: set[str] = set()
    for index in range(4):
        entry = registry.async_get_or_create(
            domain="light",
            platform="exposure_churn_nightly",
            unique_id=f"exposure-churn-{index}",
            suggested_object_id=f"exposure_churn_{index}",
        )
        hass.states.async_set(
            entry.entity_id, "on", {"friendly_name": f"Churn {index}"}
        )
        async_expose_entity(hass, conversation.DOMAIN, entry.entity_id, True)
        entities.append(
            {
                "id": entry.entity_id,
                "index": index,
                "visible": True,
                "present": True,
                "disabled": False,
            }
        )

    operations = ("exposure", "rename", "disable", "delete")
    for round_id in range(rounds):
        item = entities[rng.randrange(len(entities))]
        operation = operations[round_id % len(operations)]
        entity_id = item["id"]
        if operation == "exposure" and item["present"]:
            item["visible"] = not item["visible"]
            async_expose_entity(hass, conversation.DOMAIN, entity_id, item["visible"])
        elif operation == "rename" and item["present"]:
            renamed = f"light.exposure_churn_{item['index']}_r{round_id}"
            registry.async_update_entity(entity_id, new_entity_id=renamed)
            hass.states.async_remove(entity_id)
            async_expose_entity(hass, conversation.DOMAIN, entity_id, False)
            historical_ids.add(entity_id)
            item["id"] = renamed
            if not item["disabled"]:
                hass.states.async_set(renamed, "on")
            async_expose_entity(hass, conversation.DOMAIN, renamed, item["visible"])
        elif operation == "disable" and item["present"]:
            item["disabled"] = not item["disabled"]
            registry.async_update_entity(
                entity_id,
                disabled_by=RegistryEntryDisabler.USER if item["disabled"] else None,
            )
            if item["disabled"]:
                hass.states.async_remove(entity_id)
            else:
                hass.states.async_set(entity_id, "on")
        elif operation == "delete":
            if item["present"]:
                registry.async_remove(entity_id)
                hass.states.async_remove(entity_id)
                async_expose_entity(hass, conversation.DOMAIN, entity_id, False)
                historical_ids.add(entity_id)
                item["present"] = False
            else:
                recreated = registry.async_get_or_create(
                    domain="light",
                    platform="exposure_churn_nightly",
                    unique_id=f"exposure-churn-{item['index']}",
                    suggested_object_id=f"exposure_churn_{item['index']}_r{round_id}",
                )
                item["id"] = recreated.entity_id
                item["present"] = True
                item["disabled"] = False
                item["visible"] = True
                historical_ids.discard(recreated.entity_id)
                hass.states.async_set(recreated.entity_id, "on")
                async_expose_entity(
                    hass, conversation.DOMAIN, recreated.entity_id, True
                )
        await hass.async_block_till_done()
        results = await asyncio.gather(
            *(
                _say(hass, agent, users[(round_id + index) % len(users)].id, round_id)
                for index, agent in enumerate(agents)
            )
        )
        assert all(_speech(result) == "Current HA view received." for result in results)
        for wire in wires:
            assert len(wire.requests) == round_id + 1
            body = json.dumps(wire.requests[-1]["body"])
            for current in entities:
                visible = (
                    current["present"]
                    and current["visible"]
                    and not current["disabled"]
                )
                assert _contains_id(body, current["id"]) is visible, (
                    round_id,
                    operation,
                    current["id"],
                )
            assert all(not _contains_id(body, old_id) for old_id in historical_ids)
        record(
            stress_trace,
            "exposure_churn",
            layer="Real HA and provider wire",
            round=round_id,
            operation=operation,
            active_entities=sum(item["present"] for item in entities),
        )
    assert all(len(wire.requests) == rounds for wire in wires)
    record(
        stress_trace,
        "summary",
        layer="Real HA and provider wire",
        exposure_mutation_rounds=rounds,
        public_turns=2 * rounds,
        agents=2,
        users=len(users),
    )
