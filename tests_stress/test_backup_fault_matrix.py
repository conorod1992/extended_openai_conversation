"""Every durable restore category must roll back as one transaction."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses import (
    agent_config,
    backup,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    async_get_archive,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    async_get_guest_mode,
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
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    async_get_temporary_memory,
)
from custom_components.extended_openai_conversation_responses.usage import (
    RequestUsage,
    async_get_durable_usage,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from tests_stress.conftest import record
from tests_stress.test_backup_inventory import BACKED_UP_SUBSYSTEMS

PHASES = (
    "memory",
    "temporary_memory",
    "knowledge",
    "archive",
    "usage",
    "guest_mode",
    "request_rules",
)


@pytest.mark.asyncio
async def test_populated_export_mutate_restore_is_semantically_equal(
    hass: HomeAssistant,
    stress_trace: list[dict],
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Round trip",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {
                    CONF_CHAT_MODEL: "gpt-5.6",
                    CONF_PROMPT: "Answer in English and preserve café 🎯.",
                    CONF_KNOWLEDGE_ENABLED: True,
                    CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
                    "api_mode": "chat_completions",
                    "max_tokens": 900,
                    "reasoning_effort": "medium",
                    "temperature": 0.3,
                    "top_p": 0.8,
                    "current_datetime_enabled": False,
                    "exposed_entities_enabled": False,
                    "context_threshold": 28000,
                    "conversation_timeout_minutes": 60,
                    "archive_enabled": True,
                    "archive_retention_days": 7,
                    "web_search": True,
                    "temporary_memory": "balanced",
                    "guest_knowledge_policy": "off",
                    "guest_excluded_domains": ["lock"],
                    "usage_request_retention_days": 7,
                    "speech_processing_enabled": True,
                    "speech_strip_markdown": False,
                    "function_tool_error_recovery": True,
                },
                "subentry_type": "conversation",
                "title": "Archive 🎯 agent",
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
    blank = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    rules = await async_get_request_rules(hass, entry.entry_id, subentry.subentry_id)
    temporary = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    guest = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
    usage = await async_get_durable_usage(hass, entry.entry_id, subentry.subentry_id)
    await memory.async_add(
        "owner", "Distinct private memory 東京", "acceptance", "explicit"
    )
    await knowledge.async_create(
        "Reference 🎯", "multiline description", 'Line one\n{"json": true}\nLine three'
    )
    await rules.async_create(
        {
            "name": "Rule café",
            "phrases": ["remember {fact}"],
            "match_type": "sentence_pattern",
            "action_type": "local_action",
            "action": {"actions": [{"action": "script.turn_on"}]},
        }
    )
    await temporary.async_add(
        "user:owner",
        "Temporary round-trip marker 🕒",
        (dt_util.utcnow() + timedelta(hours=2)).isoformat(),
        "acceptance",
        owner_scope_id="user:owner",
    )
    await guest.async_update_trusted(indefinite=True)
    async with usage.async_run(home_assistant_conversation_id="backup-journey"):
        await usage.async_record_request(
            successful=True,
            usage=RequestUsage(input_tokens=12, output_tokens=4, total_tokens=16),
            provider="openai",
            model="gpt-5.6",
            api_mode="chat_completions",
            request_stage="initial",
            tool_calls_requested=0,
        )
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    defaults = agent_config.agent_config_defaults()
    nondefault_config_fields = sum(
        value != defaults.get(key) for key, value in target["agent"]["config"].items()
    )
    assert nondefault_config_fields >= 15
    assert (
        set(target) - {"format", "version", "created_at", "integration_version"}
        == BACKED_UP_SUBSYSTEMS
    )
    record(
        stress_trace,
        "export",
        memory_records=1,
        knowledge_sources=1,
        request_rules=1,
        temporary_memories=1,
        guest_mode_schedules=1,
        usage_requests=1,
        nondefault_config_fields=nondefault_config_fields,
    )
    assert (await backup.async_restore_backup(hass, entry, subentry, blank))[
        "status"
    ] == "restored"
    hass.config_entries.async_update_subentry(
        entry, subentry, title="Mutated agent", data={}
    )
    await hass.async_block_till_done()
    assert semantic(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    ) != semantic(target)
    record(stress_trace, "mutate_all_populated_features")

    assert (await backup.async_restore_backup(hass, entry, subentry, target))[
        "status"
    ] == "restored"
    await hass.async_block_till_done()
    assert semantic(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    ) == semantic(target)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert semantic(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    ) == semantic(target)
    record(stress_trace, "summary", restore_round_trips=1, reloads=1)


def semantic(snapshot: dict) -> dict:
    result = deepcopy(snapshot)
    result.pop("created_at", None)
    return result


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.asyncio
async def test_every_restore_phase_rolls_back_and_reloads(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    stress_trace: list[dict],
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fault matrix",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Fault matrix agent",
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
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)

    await memory.async_add("owner", "target memory 🎯", "acceptance", "explicit")
    await knowledge.async_create(
        "Target knowledge", "before mutation", "target content"
    )
    target = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    assert (
        set(target) - {"format", "version", "created_at", "integration_version"}
        == BACKED_UP_SUBSYSTEMS
    )
    for item in await memory.async_list("owner"):
        assert await memory.async_delete("owner", [item.memory_id]) == 1
    for item in await knowledge.async_list():
        assert await knowledge.async_delete(item["source_id"])
    await memory.async_add("owner", "current memory", "acceptance", "explicit")
    await knowledge.async_create(
        "Current knowledge", "after mutation", "current content"
    )
    before = semantic(await backup.async_collect_backup_snapshot(hass, entry, subentry))
    assert before != semantic(target)

    manager_getters = {
        "memory": async_get_memory,
        "temporary_memory": async_get_temporary_memory,
        "knowledge": async_get_knowledge,
        "archive": async_get_archive,
        "usage": async_get_durable_usage,
        "guest_mode": async_get_guest_mode,
        "request_rules": async_get_request_rules,
    }
    manager = await manager_getters[phase](hass, entry.entry_id, subentry.subentry_id)
    original = manager.async_replace_backup
    calls = 0

    async def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(f"injected {phase} apply failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "async_replace_backup", fail_once)
    record(stress_trace, "restore_fault", phase=phase)
    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await backup.async_restore_backup(hass, entry, subentry, target)
    assert calls == 2
    await hass.async_block_till_done()
    assert (
        semantic(await backup.async_collect_backup_snapshot(hass, entry, subentry))
        == before
    )
    assert entry.state is ConfigEntryState.LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        semantic(await backup.async_collect_backup_snapshot(hass, entry, subentry))
        == before
    )
    record(stress_trace, "summary", rollback_phases=1, reloads=1)
