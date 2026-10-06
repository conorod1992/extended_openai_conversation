"""Independent secondary observations for selected critical EOAI outcomes."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire, _speech
from tests_real_ha.test_request_rules_script_semantics import _local, _record_action
from tests_stress.effect_ledger import assert_effect_ledger


async def _say(hass, agent, text, *, user_id=None):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


async def _fresh_memory(hass, agent):
    memory = PersistentMemory(
        HomeAssistantMemoryStorage(
            hass,
            agent.entry.entry_id,
            agent.subentry.subentry_id,
        )
    )
    await memory.async_initialize()
    return memory


async def test_memory_delete_is_absent_from_live_and_fresh_durable_views(hass):
    """Deletion must disappear both from the live manager and a fresh Store consumer."""
    agent = await _agent(hass, **{CONF_MEMORY_MODE: MEMORY_MODE_MANUAL})
    owner = await hass.auth.async_create_user("Independent deletion owner")
    created = await agent._memory.async_add(
        owner.id,
        "DELETION WITNESS should disappear",
        "nightly",
        "explicit",
    )
    memory_id = created["memory"]["memory_id"]

    assert any(
        item.memory_id == memory_id for item in await agent._memory.async_list(owner.id)
    )
    assert await agent._memory.async_delete(owner.id, [memory_id]) == 1
    assert all(
        item.memory_id != memory_id for item in await agent._memory.async_list(owner.id)
    )

    fresh = await _fresh_memory(hass, agent)
    assert all(item.memory_id != memory_id for item in await fresh.async_list(owner.id))


async def test_memory_ownership_is_independently_preserved_in_fresh_store(hass):
    """Two owners can agree in RAM only if the durable owner mapping is also correct."""
    agent = await _agent(hass, **{CONF_MEMORY_MODE: MEMORY_MODE_MANUAL})
    first = await hass.auth.async_create_user("Independent owner one")
    second = await hass.auth.async_create_user("Independent owner two")
    await agent._memory.async_add(first.id, "OWNER ONE WITNESS", "nightly", "explicit")
    await agent._memory.async_add(second.id, "OWNER TWO WITNESS", "nightly", "explicit")

    assert [item.content for item in await agent._memory.async_list(first.id)] == [
        "OWNER ONE WITNESS"
    ]
    assert [item.content for item in await agent._memory.async_list(second.id)] == [
        "OWNER TWO WITNESS"
    ]

    fresh = await _fresh_memory(hass, agent)
    assert [item.content for item in await fresh.async_list(first.id)] == [
        "OWNER ONE WITNESS"
    ]
    assert [item.content for item in await fresh.async_list(second.id)] == [
        "OWNER TWO WITNESS"
    ]


async def test_rule_precedence_matches_ordered_native_effects_and_persisted_order(
    hass, monkeypatch
):
    """Public response, native effects and raw durable order must agree."""
    agent = await _agent(hass)
    effects = []

    async def record(call):
        effects.append(call.data["message"])

    hass.services.async_register("independent_probe", "record", record)
    first = _local(
        [{"action": "independent_probe.record", "data": {"message": "first"}}],
        phrase="priority witness",
        success="First",
    )
    first["continue_matching"] = True
    first["order"] = 0
    second = _local(
        [{"action": "independent_probe.record", "data": {"message": "second"}}],
        phrase="priority witness",
        success="Second",
    )
    second["order"] = 1
    first_rule = await agent._request_rules.async_create(first)
    second_rule = await agent._request_rules.async_create(second)

    result = await _say(hass, agent, "priority witness")
    assert _speech(result) == "Second"
    assert effects == ["first", "second"]

    persisted = await agent._request_rules._store.async_load()
    stored_rules = persisted["rules"]
    assert [row["id"] for row in stored_rules[:2]] == [
        first_rule["id"],
        second_rule["id"],
    ]


async def test_rule_provider_handoff_has_wire_request_and_no_local_effect(
    hass, monkeypatch
):
    """A routed success cannot be mistaken for a local action that never occurred."""
    agent = await _agent(hass)
    effects = []

    async def record(call):
        effects.append(call.data)

    hass.services.async_register("independent_probe", "record", record)
    rule = _local(
        [_record_action("must-not-run")],
        phrase="provider handoff witness",
        success="Unused",
    )
    rule["action_type"] = "model_routing"
    rule["action"] = {
        "model": "gpt-5.6",
        "reasoning_effort": "none",
        "scope": "request",
        "reset": False,
        "continue_to_ai": True,
    }
    await agent._request_rules.async_create(rule)
    wire = _install_wire(monkeypatch, agent, [_chat_sse_text("Provider handoff complete")])

    result = await _say(hass, agent, "provider handoff witness")
    assert _speech(result) == "Provider handoff complete"
    assert len(wire.requests) == 1
    assert wire.requests[0]["body"]["model"] == "gpt-5.6"
    assert effects == []


async def test_restored_rule_is_durable_and_executable_after_reload(hass):
    """A reported restore is independently witnessed by later rule behaviour."""
    agent = await _agent(hass)
    effects = []

    async def record(call):
        effects.append(call.data["message"])

    hass.services.async_register("independent_probe", "record", record)
    rule = await agent._request_rules.async_create(
        _local(
            [
                {
                    "action": "independent_probe.record",
                    "data": {"message": "RESTORED RULE EFFECT"},
                }
            ],
            phrase="restored rule witness",
            success="Restored rule response",
        )
    )
    snapshot = await backup.async_collect_backup_snapshot(
        hass, agent.entry, agent.subentry
    )
    assert await agent._request_rules.async_delete(rule["id"])
    assert (
        await backup.async_restore_backup(
            hass, agent.entry, agent.subentry, deepcopy(snapshot)
        )
    )["status"] == "restored"
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    reloaded = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert reloaded is not None
    result = await _say(hass, reloaded, "restored rule witness")
    assert _speech(result) == "Restored rule response"
    assert effects == ["RESTORED RULE EFFECT"]


async def test_failed_restore_keeps_current_rule_behavior_after_reload(hass, monkeypatch):
    """A failed restore must not partially publish the target rule set."""
    agent = await _agent(hass, **{CONF_KNOWLEDGE_ENABLED: True})
    effects = []

    async def record(call):
        effects.append(call.data["message"])

    hass.services.async_register("independent_probe", "record", record)
    target_rule = await agent._request_rules.async_create(
        _local(
            [
                {
                    "action": "independent_probe.record",
                    "data": {"message": "TARGET RULE EFFECT"},
                }
            ],
            phrase="restore failure witness",
            success="Target rule response",
        )
    )
    await agent._knowledge.async_create(
        "Target restore witness", "target", "TARGET KNOWLEDGE"
    )
    target = await backup.async_collect_backup_snapshot(
        hass, agent.entry, agent.subentry
    )

    assert await agent._request_rules.async_delete(target_rule["id"])
    for row in await agent._knowledge.async_list():
        assert await agent._knowledge.async_delete(row["source_id"])
    await agent._request_rules.async_create(
        _local(
            [
                {
                    "action": "independent_probe.record",
                    "data": {"message": "CURRENT RULE EFFECT"},
                }
            ],
            phrase="restore failure witness",
            success="Current rule response",
        )
    )

    original = agent._knowledge.async_replace_backup
    calls = 0

    async def fail_once(records):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("independent restore failure")
        return await original(records)

    monkeypatch.setattr(agent._knowledge, "async_replace_backup", fail_once)
    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await backup.async_restore_backup(
            hass, agent.entry, agent.subentry, deepcopy(target)
        )
    assert calls == 2
    await hass.async_block_till_done()

    live = await _say(hass, agent, "restore failure witness")
    assert _speech(live) == "Current rule response"
    assert effects == ["CURRENT RULE EFFECT"]

    assert await hass.config_entries.async_reload(agent.entry.entry_id)
    await hass.async_block_till_done()
    reloaded = conversation.async_get_agent(hass, agent.entry.entry_id)
    assert reloaded is not None
    after_reload = await _say(hass, reloaded, "restore failure witness")
    assert _speech(after_reload) == "Current rule response"
    assert effects == ["CURRENT RULE EFFECT", "CURRENT RULE EFFECT"]


@pytest.mark.parametrize(
    "mutated",
    [
        {"effects": []},
        {"effects": [("service", "other-owner", {"entity_id": "light.one"})]},
        {
            "effects": [
                ("service", "owner", {"entity_id": "light.two"}),
                ("service", "owner", {"entity_id": "light.one"}),
            ]
        },
        {"effects": [("service", "owner", {"entity_id": "light.one"})], "durable": []},
    ],
    ids=["suppressed-effect", "wrong-owner-target", "reordered", "missing-persistence"],
)
def test_effect_ledger_sensitivity_rejects_representative_false_success(mutated):
    """Representative false-success mutations must be visible to the assertions."""
    expected = {
        "effects": [
            ("service", "owner", {"entity_id": "light.one"}),
            ("service", "owner", {"entity_id": "light.two"}),
        ],
        "durable": ["committed"],
    }
    candidate = deepcopy(expected)
    candidate.update(mutated)
    with pytest.raises(AssertionError):
        assert_effect_ledger(candidate, expected)
