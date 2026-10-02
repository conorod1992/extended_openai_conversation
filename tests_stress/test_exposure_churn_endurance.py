"""Seeded HA registry and Assist exposure churn at the provider wire."""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    CONF_EXPOSED_ENTITIES_ENABLED,
)
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
        text=f"Describe available entities for {agent.entry.title} at round {round_id}",
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
    agents = [
        await _agent(
            hass,
            title=f"Exposure Churn {index}",
            **{CONF_EXPOSED_ENTITIES_ENABLED: True},
        )
        for index in range(2)
    ]
    assert agents[0].entry.entry_id != agents[1].entry.entry_id
    rounds = 12 if stress_scale == 1 else 36
    wire = _install_wire(
        monkeypatch,
        agents[0],
        [_chat_sse_text("Current HA view received.")] * (2 * rounds),
    )
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
        record(
            stress_trace,
            "exposure_churn",
            layer="Real HA and provider wire",
            round=round_id,
            mutation=operation,
            target=entity_id,
            active_entities=sum(item["present"] for item in entities),
        )
        assert len(wire.requests) == 2 * (round_id + 1)
        latest = wire.requests[-2:]
        for agent in agents:
            assert (
                sum(
                    agent.entry.title in json.dumps(request["body"])
                    for request in latest
                )
                == 1
            )
        for request in latest:
            body = json.dumps(request["body"])
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
    assert len(wire.requests) == 2 * rounds
    record(
        stress_trace,
        "summary",
        layer="Real HA and provider wire",
        exposure_mutation_rounds=rounds,
        public_turns=2 * rounds,
        agents=2,
        users=len(users),
    )


async def test_privileged_warm_cache_respects_later_restricted_registry_and_group_changes(
    hass, monkeypatch, stress_trace
):
    """Natural later requests filter current HA read policy and independently gate control."""
    from homeassistant.auth.models import Group
    from homeassistant.auth.permissions.const import (
        CAT_ENTITIES,
        POLICY_READ,
        POLICY_CONTROL,
    )
    from homeassistant.auth.permissions.entities import ENTITY_ENTITY_IDS
    from homeassistant.auth.const import GROUP_ID_READ_ONLY, GROUP_ID_USER
    from tests_real_ha.test_provider_wire_e2e import _agent as wire_agent
    from tests_real_ha.test_user_permission_acceptance import (
        _chat_sse_tool_call,
        _tool_result_from_chat_request,
    )

    owner = MockUser(id="cache-owner", is_owner=True).add_to_hass(hass)
    registry = er.async_get(hass)
    allowed = registry.async_get_or_create(
        domain="light",
        platform="permission_churn",
        unique_id="allowed",
        suggested_object_id="cache_allowed",
    )
    denied = registry.async_get_or_create(
        domain="light",
        platform="permission_churn",
        unique_id="denied",
        suggested_object_id="cache_denied",
    )
    current_id = denied.entity_id
    group = Group(
        id="cache-restricted-policy",
        name="Cache policy",
        policy={
            CAT_ENTITIES: {
                ENTITY_ENTITY_IDS: {
                    allowed.entity_id: {POLICY_READ: True, POLICY_CONTROL: True},
                    denied.entity_id: {POLICY_READ: True},
                }
            }
        },
    )
    user = MockUser(
        id="cache-restricted-user", groups=[group], is_owner=False
    ).add_to_hass(hass)
    from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
        update_live_subentry,
    )

    agent = await wire_agent(hass, "chat_completions")
    update_live_subentry(
        hass,
        agent.entry,
        agent.subentry,
        data={**agent.subentry.data, CONF_EXPOSED_ENTITIES_ENABLED: True},
    )
    for item in (allowed, denied):
        hass.states.async_set(item.entity_id, "on")
        async_expose_entity(hass, conversation.DOMAIN, item.entity_id, True)
    effects = []

    async def off(call):
        effects.append(call)

    hass.services.async_register("light", "turn_off", off)

    async def read_as(actor, label):
        wire = _install_wire(
            monkeypatch, agent, [_chat_sse_text("Current permission context")]
        )
        assert (
            _speech(await _say(hass, agent, actor.id, label))
            == "Current permission context"
        )
        return json.dumps(wire.requests[0]["body"])

    mutations = (
        "warm",
        "rename",
        "disable",
        "enable",
        "delete",
        "recreate",
        "unexpose",
        "expose",
        "no-groups",
        "read-only",
        "user-group",
    )
    stale_ids = set()
    for index, mutation in enumerate(mutations):
        if mutation == "rename":
            stale_ids.add(current_id)
            registry.async_update_entity(
                current_id, new_entity_id="light.cache_renamed"
            )
            hass.states.async_remove(current_id)
            async_expose_entity(hass, conversation.DOMAIN, current_id, False)
            current_id = "light.cache_renamed"
            hass.states.async_set(current_id, "on")
            async_expose_entity(hass, conversation.DOMAIN, current_id, True)
        elif mutation in {"disable", "enable"}:
            registry.async_update_entity(
                current_id,
                disabled_by=RegistryEntryDisabler.USER
                if mutation == "disable"
                else None,
            )
            if mutation == "disable":
                hass.states.async_remove(current_id)
            else:
                hass.states.async_set(current_id, "on")
        elif mutation == "delete":
            registry.async_remove(current_id)
            hass.states.async_remove(current_id)
            async_expose_entity(hass, conversation.DOMAIN, current_id, False)
            stale_ids.add(current_id)
        elif mutation == "recreate":
            recreated = registry.async_get_or_create(
                domain="light",
                platform="permission_churn",
                unique_id="denied",
                suggested_object_id="cache_recreated",
            )
            current_id = recreated.entity_id
            stale_ids.discard(current_id)
            hass.states.async_set(current_id, "on")
            async_expose_entity(hass, conversation.DOMAIN, current_id, True)
        elif mutation in {"expose", "unexpose"}:
            async_expose_entity(
                hass, conversation.DOMAIN, current_id, mutation == "expose"
            )
        elif mutation in {"no-groups", "read-only", "user-group"}:
            await hass.auth.async_update_user(
                user,
                group_ids=[]
                if mutation == "no-groups"
                else [GROUP_ID_READ_ONLY if mutation == "read-only" else GROUP_ID_USER],
            )
        await hass.async_block_till_done()
        privileged = await read_as(owner, index * 2)
        restricted = await read_as(user, index * 2 + 1)
        assert all(not _contains_id(restricted, old) for old in stale_ids)
        assert _contains_id(restricted, allowed.entity_id) is (mutation != "no-groups")
        permitted_denied = mutation in {"warm", "read-only", "user-group"}
        assert _contains_id(restricted, current_id) is permitted_denied
        if mutation in {"warm", "read-only"}:
            denied_wire = _install_wire(
                monkeypatch,
                agent,
                [
                    _chat_sse_tool_call(current_id, f"denied-{index}"),
                    _chat_sse_text("Control refused"),
                ],
            )
            assert _speech(await _say(hass, agent, user.id, index)) == "Control refused"
            assert "does not have permission to control" in json.dumps(
                _tool_result_from_chat_request(
                    denied_wire.requests[1]["body"], f"denied-{index}"
                )
            )
            assert effects == []
        if mutation not in {"disable", "delete", "unexpose"}:
            assert _contains_id(privileged, current_id)
    allowed_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(allowed.entity_id, "allowed-after-churn"),
            _chat_sse_text("Control recovered"),
        ],
    )
    assert _speech(await _say(hass, agent, user.id, 99)) == "Control recovered"
    assert len(effects) == 1 and effects[0].context.user_id == user.id
    assert _tool_result_from_chat_request(
        allowed_wire.requests[1]["body"], "allowed-after-churn"
    )["result"][0]["success"]
    record(
        stress_trace,
        "summary",
        layer="Real HA provider",
        permission_cache_churn_cases=1,
        restricted_context_probes=len(mutations),
        permission_group_updates=3,
        recovery_conversations=1,
    )
