"""Seeded valid mutations across independent durable agent stores and reloads."""

from __future__ import annotations

import random

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context, HomeAssistant
from tests_stress.conftest import record


def _semantic(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != "created_at"}


@pytest.mark.asyncio
async def test_seeded_cross_store_chaos_preserves_valid_agent_state(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_seed: int,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    rng = random.Random(stress_seed ^ 0xC4A05)
    for number in range(4):
        MockUser(id=f"chaos-user-{number}", name=f"Chaos user {number}").add_to_hass(
            hass
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Chaos agent",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {
                    CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
                    CONF_KNOWLEDGE_ENABLED: True,
                },
                "subentry_type": "conversation",
                "title": "Chaos conversation",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    checkpoints: list[dict] = []
    turns = 0

    async def managers():
        return (
            await async_get_memory(hass, entry.entry_id, subentry.subentry_id),
            await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id),
            await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id),
        )

    for step in range(90 * stress_scale):
        memory, knowledge, rules = await managers()
        operation = rng.choice(
            (
                "memory_add",
                "memory_add",
                "memory_delete",
                "knowledge_create",
                "knowledge_delete",
                "rule_create",
                "rule_delete",
                "checkpoint",
                "restore",
                "reload",
            )
        )
        users = [f"chaos-user-{number}" for number in range(4)]
        if operation == "memory_add":
            user = rng.choice(users)
            record(stress_trace, operation, step=step, user=user)
            await memory.async_add(
                user,
                f"chaos marker {user} operation {step}",
                "acceptance",
                "explicit",
                key=f"chaos-{step}",
            )
        elif operation == "memory_delete":
            user = rng.choice(users)
            items = await memory.async_list(user, limit=50)
            if items:
                record(
                    stress_trace, operation, step=step, user=user, id=items[0].memory_id
                )
                assert await memory.async_delete(user, [items[0].memory_id]) == 1
        elif operation == "knowledge_create":
            record(stress_trace, operation, step=step)
            await knowledge.async_create(
                f"Chaos title {step % 3}",
                "valid description",
                f"Knowledge marker {step} 東京",
            )
        elif operation == "knowledge_delete":
            items = await knowledge.async_list()
            if items:
                selected = rng.choice(items)
                record(stress_trace, operation, step=step, id=selected["source_id"])
                assert await knowledge.async_delete(selected["source_id"])
        elif operation == "rule_create":
            record(stress_trace, operation, step=step)
            await rules.async_create(
                {
                    "name": f"Chaos rule {step}",
                    "phrases": [f"local command {step}"],
                    "match_type": "equals",
                    "action_type": "local_action",
                    "action": {"actions": [{"action": "script.turn_on"}]},
                }
            )
        elif operation == "rule_delete":
            items = rules.snapshot()["rules"]
            if items:
                selected = rng.choice(items)
                record(stress_trace, operation, step=step, id=selected["id"])
                assert await rules.async_delete(selected["id"])
        elif operation == "checkpoint":
            checkpoints.append(
                await backup.async_collect_backup_snapshot(hass, entry, subentry)
            )
            record(stress_trace, operation, step=step, checkpoint=len(checkpoints) - 1)
        elif operation == "restore" and checkpoints:
            checkpoint = rng.choice(checkpoints)
            record(
                stress_trace,
                operation,
                step=step,
                checkpoint=checkpoints.index(checkpoint),
            )
            assert (
                await backup.async_restore_backup(hass, entry, subentry, checkpoint)
            )["status"] == "restored"
            await hass.async_block_till_done()
            assert _semantic(
                await backup.async_collect_backup_snapshot(hass, entry, subentry)
            ) == _semantic(checkpoint)
        elif operation == "reload":
            record(stress_trace, operation, step=step)
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()

        memory, knowledge, rules = await managers()
        snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)
        assert backup.inspect_backup(snapshot, subentry.subentry_id)
        for user in users:
            assert all(
                item.user_id == user for item in await memory.async_list(user, limit=50)
            )
        assert len({item["source_id"] for item in await knowledge.async_list()}) == len(
            await knowledge.async_list()
        )
        assert len({item["id"] for item in rules.snapshot()["rules"]}) == len(
            rules.snapshot()["rules"]
        )
        assert entry.state is ConfigEntryState.LOADED
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None

        async def model(
            log: conversation.ChatLog, _agent_entity_id: str = agent.entity_id, **kwargs
        ) -> None:
            del kwargs
            log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(
                    agent_id=_agent_entity_id, content="chaos healthy"
                )
            )

        monkeypatch.setattr(agent, "_async_handle_chat_log", model)
        result = await conversation.async_converse(
            hass=hass,
            text=f"probe {step}",
            conversation_id=None,
            context=Context(user_id=users[step % len(users)]),
            language="en",
            agent_id=entry.entry_id,
        )
        assert result.response.as_dict()["speech"]["plain"]["speech"] == "chaos healthy"
        turns += 1
    record(
        stress_trace,
        "summary",
        chaos_operations=90 * stress_scale,
        checkpoints=len(checkpoints),
        public_conversation_turns=turns,
    )
