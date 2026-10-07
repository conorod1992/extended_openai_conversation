"""Cross-layer reconciliation across management, runtime, storage, and Assist."""

from __future__ import annotations

import pytest

from custom_components.extended_openai_conversation_responses import agent_config
from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_KEY_PREFIX as RULES_STORAGE_KEY_PREFIX,
    STORAGE_VERSION as RULES_STORAGE_VERSION,
    RequestRuleStore,
    RequestRules,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _rule, _speech
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _conversation_subentry,
    _management_call,
)
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
async def test_management_runtime_and_durable_state_reconcile(
    hass, hass_ws_client, stress_trace
):
    """One populated assistant must agree at every authoritative state layer."""
    entry = _make_entry(
        "Reconciliation oracle",
        include_ai_task=False,
        conversation_options={
            "knowledge_enabled": True,
            "memory_mode": "manual",
            "memory_auto_retrieve_limit": 0,
        },
    )
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None

    owner = await hass.auth.async_create_user("Reconciliation owner")
    client = await _admin_client(
        hass, hass_ws_client, user_id="reconciliation-admin", name="Reconciliation Admin"
    )

    calls: list[str] = []

    async def record(call):
        calls.append(call.data["marker"])

    hass.services.async_register("reconciliation_probe", "record", record)

    memory_result = await agent._memory.async_add(
        owner.id,
        "Reconciliation memory",
        "acceptance",
        "explicit",
    )
    source = await agent._knowledge.async_create(
        "Reconciliation source",
        "Acceptance",
        "Reconciliation knowledge",
    )
    created_rule = await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [
                    {
                        "action": "reconciliation_probe.record",
                        "data": {"marker": "runtime-rule-fired"},
                    }
                ],
                "success_response": "Reconciled locally",
                "failure_response": "Reconciliation failed",
            },
            phrase="reconciliation command",
        )
    )
    await hass.async_block_till_done()

    config_ws = await _management_call(
        client, entry=entry, section="configuration", action="get"
    )
    memory_ws = await _management_call(
        client,
        entry=entry,
        section="memories",
        action="list",
        scope_id=f"user:{owner.id}",
    )
    knowledge_ws = await _management_call(
        client, entry=entry, section="knowledge", action="list"
    )
    rules_ws = await _management_call(
        client, entry=entry, section="request_rules", action="list"
    )

    # Management and active runtime must describe the same configuration.
    assert agent_config.normalize_agent_config(config_ws["config"]) == (
        agent_config.normalize_agent_config(subentry.data)
    )

    memory_id = memory_result["memory"]["memory_id"]
    assert {
        item["memory_id"]: item["content"] for item in memory_ws["memories"]
    }[memory_id] == "Reconciliation memory"
    assert {
        item["source_id"]: item["title"] for item in knowledge_ws["sources"]
    }[source.source_id] == "Reconciliation source"
    assert {
        item["id"]: item["name"] for item in rules_ws["rules"]
    }[created_rule["id"]] == created_rule["name"]

    # Fresh readers bypass the active managers and prove durable state independently.
    fresh_memory = PersistentMemory(
        HomeAssistantMemoryStorage(hass, entry.entry_id, subentry.subentry_id)
    )
    await fresh_memory.async_initialize()
    disk_memories = await fresh_memory.async_list(owner.id, limit=100)
    assert {item.memory_id: item.content for item in disk_memories}[memory_id] == (
        "Reconciliation memory"
    )

    fresh_knowledge = KnowledgeLibrary(
        HomeAssistantKnowledgeStorage(hass, entry.entry_id, subentry.subentry_id)
    )
    await fresh_knowledge.async_initialize()
    disk_sources = await fresh_knowledge.async_list()
    assert {
        item["source_id"]: item["title"] for item in disk_sources
    }[source.source_id] == "Reconciliation source"

    fresh_rules = RequestRules(
        RequestRuleStore(
            hass,
            RULES_STORAGE_VERSION,
            f"{RULES_STORAGE_KEY_PREFIX}.{entry.entry_id}.{subentry.subentry_id}",
        ).bind_agent(entry.entry_id, subentry.subentry_id)
    )
    await fresh_rules.async_initialize()
    assert {
        item["id"]: item["name"] for item in fresh_rules.snapshot()["rules"]
    }[created_rule["id"]] == created_rule["name"]

    # Finally prove the state represented above is the state the public consumer uses.
    result = await conversation.async_converse(
        hass=hass,
        text="reconciliation command",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert _speech(result) == "Reconciled locally"
    assert calls == ["runtime-rule-fired"]

    stress_trace.append(
        {
            "operation": "cross_layer_reconciliation",
            "configuration": True,
            "memory": True,
            "knowledge": True,
            "request_rules": True,
            "public_assist": True,
        }
    )
