"""Seeded cross-subsystem mutation model through the real management API."""

from __future__ import annotations

import random

from homeassistant.components import conversation
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_cross_feature_acceptance import _rule
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _conversation_subentry,
    _fresh_reload,
    _management_call,
)
from tests_stress.conftest import record


async def test_seeded_cross_subsystem_management_state_machine(
    hass,
    hass_ws_client,
    stress_seed,
    stress_scale,
    stress_trace,
):
    """Generated operation sequences must agree with an independent state model."""
    rng = random.Random(stress_seed ^ 0xC2055)
    entry = _make_entry(
        "Cross-subsystem state machine",
        include_ai_task=False,
        conversation_options={
            "knowledge_enabled": True,
            "memory_mode": "manual",
            "memory_auto_retrieve_limit": 0,
            "advanced_options": False,
        },
    )
    await _setup_entry(hass, entry)
    owner = await hass.auth.async_create_user("State machine owner")
    client = await _admin_client(
        hass,
        hass_ws_client,
        user_id="cross-subsystem-state-admin",
        name="Cross Subsystem State Admin",
    )
    marker = f"State {stress_seed}"
    model = {
        "advanced_options": False,
        "memories": {},
        "knowledge": {},
        "rules": {},
    }
    serial = 0

    async def checkpoint(step: int) -> None:
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        config = await _management_call(
            client, entry=entry, section="configuration", action="get"
        )
        assert bool(config["config"].get("advanced_options", False)) is model[
            "advanced_options"
        ]

        memories = await _management_call(
            client,
            entry=entry,
            section="memories",
            action="list",
            scope_id=f"user:{owner.id}",
        )
        actual_memories = {
            item["memory_id"]: item["content"]
            for item in memories["memories"]
            if item["content"].startswith(marker)
        }
        assert actual_memories == model["memories"]
        runtime_memories = await agent._memory.async_list(owner.id, limit=500)
        assert {
            item.memory_id: item.content
            for item in runtime_memories
            if item.content.startswith(marker)
        } == model["memories"]

        knowledge = await _management_call(
            client, entry=entry, section="knowledge", action="list"
        )
        actual_knowledge = {
            item["source_id"]: item["title"]
            for item in knowledge["sources"]
            if item["title"].startswith(marker)
        }
        assert actual_knowledge == model["knowledge"]
        runtime_knowledge = await agent._knowledge.async_list()
        assert {
            item["source_id"]: item["title"]
            for item in runtime_knowledge
            if item["title"].startswith(marker)
        } == model["knowledge"]

        rules = await _management_call(
            client, entry=entry, section="request_rules", action="list"
        )
        actual_rules = {
            item["id"]: item["name"]
            for item in rules["rules"]
            if item["name"].startswith(marker)
        }
        assert actual_rules == model["rules"]
        assert {
            item["id"]: item["name"]
            for item in agent._request_rules.snapshot()["rules"]
            if item["name"].startswith(marker)
        } == model["rules"]
        record(
            stress_trace,
            "cross_subsystem_checkpoint",
            step=step,
            memories=len(model["memories"]),
            knowledge=len(model["knowledge"]),
            rules=len(model["rules"]),
            advanced=model["advanced_options"],
        )

    operations = (
        "toggle_config",
        "add_memory",
        "delete_memory",
        "add_knowledge",
        "delete_knowledge",
        "add_rule",
        "delete_rule",
        "reload",
    )
    for step in range(48 * stress_scale):
        operation = rng.choice(operations)
        serial += 1

        if operation == "toggle_config":
            current = await _management_call(
                client, entry=entry, section="configuration", action="get"
            )
            desired = not model["advanced_options"]
            updated = dict(current["config"])
            updated["advanced_options"] = desired
            await _management_call(
                client,
                entry=entry,
                section="configuration",
                action="update",
                revision=current["revision"],
                config=updated,
            )
            model["advanced_options"] = desired

        elif operation == "add_memory" or (
            operation == "delete_memory" and not model["memories"]
        ):
            content = f"{marker} memory {serial}"
            result = await _management_call(
                client,
                entry=entry,
                section="memories",
                action="add",
                scope_id=f"user:{owner.id}",
                content=content,
                category="state-machine",
            )
            item = result["memory"]
            model["memories"][item["memory_id"]] = content

        elif operation == "delete_memory":
            memory_id = rng.choice(tuple(model["memories"]))
            await _management_call(
                client,
                entry=entry,
                section="memories",
                action="delete",
                scope_id=f"user:{owner.id}",
                memory_id=memory_id,
            )
            model["memories"].pop(memory_id)

        elif operation == "add_knowledge" or (
            operation == "delete_knowledge" and not model["knowledge"]
        ):
            title = f"{marker} source {serial}"
            result = await _management_call(
                client,
                entry=entry,
                section="knowledge",
                action="create",
                title=title,
                content=f"body {serial}",
                enabled=True,
            )
            item = result["source"]
            model["knowledge"][item["source_id"]] = title

        elif operation == "delete_knowledge":
            source_id = rng.choice(tuple(model["knowledge"]))
            await _management_call(
                client,
                entry=entry,
                section="knowledge",
                action="delete",
                source_id=source_id,
            )
            model["knowledge"].pop(source_id)

        elif operation == "add_rule" or (
            operation == "delete_rule" and not model["rules"]
        ):
            current = await _management_call(
                client, entry=entry, section="request_rules", action="list"
            )
            rule_id = f"state-rule-{stress_seed}-{serial}"
            rule = {
                **_rule(
                    "model_routing",
                    {
                        "model": "gpt-5-mini",
                        "reasoning_effort": "medium",
                        "scope": "request",
                        "reset": False,
                        "continue_to_ai": True,
                        "success_response": "Routed",
                    },
                    phrase=f"state route {serial}",
                ),
                "id": rule_id,
                "name": f"{marker} rule {serial}",
                "order": len(current["rules"]),
            }
            result = await _management_call(
                client,
                entry=entry,
                section="request_rules",
                action="create",
                revision=current["revision"],
                rule=rule,
            )
            model["rules"][result["rule"]["id"]] = result["rule"]["name"]

        elif operation == "delete_rule":
            rule_id = rng.choice(tuple(model["rules"]))
            current = await _management_call(
                client, entry=entry, section="request_rules", action="list"
            )
            await _management_call(
                client,
                entry=entry,
                section="request_rules",
                action="delete",
                revision=current["revision"],
                rule_id=rule_id,
                confirm=True,
            )
            model["rules"].pop(rule_id)

        else:
            await _fresh_reload(hass, entry)

        record(stress_trace, "cross_subsystem_operation", step=step, action=operation)
        await checkpoint(step)

    # End with a cold manager reconstruction, not merely the final live objects.
    await _fresh_reload(hass, entry)
    await checkpoint(48 * stress_scale)
