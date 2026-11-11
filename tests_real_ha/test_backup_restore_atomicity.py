"""Real Home Assistant regression coverage for atomic backup restore failure."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.extended_openai_conversation_responses.private_backup import encode_document
from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.agent_config import (
    parse_configured_function_tools,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import async_get_memory


def _entry() -> MockConfigEntry:
    """Build one local-only entry with durable Memory enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Backup Restore Atomicity",
        data={
            CONF_API_KEY: "sk-backup-restore-atomicity",
            CONF_SKIP_AUTHENTICATION: True,
        },
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
                "subentry_type": "conversation",
                "title": "Backup Restore Atomicity Conversation",
                "unique_id": None,
            }
        ],
    )


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Load the integration through Home Assistant's real config-entry manager."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _conversation_subentry(entry: MockConfigEntry):
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


@pytest.mark.asyncio
async def test_loaded_restore_rolls_back_earlier_subsystems_when_later_apply_fails(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later subsystem failure cannot leave an earlier restore partially applied."""
    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)

    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    owner = "backup-atomicity-owner"

    target_memory = await memory.async_add(
        owner,
        "Backup target memory that must never survive a failed restore.",
        "acceptance",
        "explicit",
    )
    target_memory_id = target_memory["memory"]["memory_id"]
    target_knowledge = await knowledge.async_create(
        "Backup target knowledge",
        "Target state for the failed restore.",
        "This knowledge belongs only to the backup target state.",
    )

    target_snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)

    assert await memory.async_delete(owner, [target_memory_id]) == 1
    current_memory = await memory.async_add(
        owner,
        "Current memory that must survive the failed restore.",
        "acceptance",
        "explicit",
    )
    current_memory_id = current_memory["memory"]["memory_id"]

    assert await knowledge.async_delete(target_knowledge.source_id)
    current_knowledge = await knowledge.async_create(
        "Current knowledge",
        "Live state immediately before restore.",
        "This knowledge must remain after rollback completes.",
    )

    memory_before = await memory.async_backup_data()
    knowledge_before = await knowledge.async_backup_data()
    # Rollback preserves stored source and omissions exactly, without adopting
    # normalized defaults. Effective runtime behavior is checked independently.
    subentry_data_before = deepcopy(dict(subentry.data))
    runtime_functions_before = parse_configured_function_tools(subentry.data)
    subentry_title_before = subentry.title

    original_memory_replace = memory.async_replace_backup
    original_knowledge_replace = knowledge.async_replace_backup
    memory_apply_contents: list[list[str]] = []
    knowledge_apply_calls = 0

    async def record_memory_replace(records: list[Any]) -> None:
        memory_apply_contents.append([record.content for record in records])
        await original_memory_replace(records)

    async def fail_first_knowledge_replace(records: list[Any]) -> None:
        nonlocal knowledge_apply_calls
        knowledge_apply_calls += 1
        if knowledge_apply_calls == 1:
            raise RuntimeError("injected later-subsystem restore failure")
        await original_knowledge_replace(records)

    monkeypatch.setattr(memory, "async_replace_backup", record_memory_replace)
    monkeypatch.setattr(knowledge, "async_replace_backup", fail_first_knowledge_replace)

    with pytest.raises(
        backup.BackupError, match="previous agent state was recovered"
    ):
        await backup.async_restore_backup(hass, entry, subentry, encode_document(target_snapshot))

    assert knowledge_apply_calls == 2
    assert len(memory_apply_contents) == 2
    assert memory_apply_contents[0] == [
        "Backup target memory that must never survive a failed restore."
    ]
    assert memory_apply_contents[1] == [
        "Current memory that must survive the failed restore."
    ]

    assert await memory.async_backup_data() == memory_before
    assert await knowledge.async_backup_data() == knowledge_before
    assert dict(subentry.data) == subentry_data_before
    assert parse_configured_function_tools(subentry.data) == runtime_functions_before
    assert subentry.title == subentry_title_before

    memories = await memory.async_list(owner)
    assert [(item.memory_id, item.content) for item in memories] == [
        (current_memory_id, "Current memory that must survive the failed restore.")
    ]
    assert await knowledge.async_get(current_knowledge.source_id) == current_knowledge
    with pytest.raises(ValueError, match="knowledge source not found"):
        await knowledge.async_get(target_knowledge.source_id)

    # Updating the subentry during rollback schedules a real config-entry reload.
    # Let that lifecycle transition finish before asking HA to unload the entry;
    # otherwise the cleanup races SETUP_IN_PROGRESS and HA correctly refuses it.
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED



@pytest.mark.parametrize("interrupted", [False, True])
async def test_cross_provider_private_restore_and_committed_replay(hass, monkeypatch, interrupted):
    from custom_components.extended_openai_conversation_responses import restore_recovery, transfer
    from custom_components.extended_openai_conversation_responses.provider_credentials import restore_private_provider_connection
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry as setup
    old_connection = {"api_provider": "azure", "base_url": "https://example.openai.azure.com", "api_version": "2025-04-01-preview", "api_key": "test-azure", "skip_authentication": True}
    new_connection = {"api_provider": "openai", "api_key": "test-openai", "skip_authentication": True}
    entry = _make_entry("Provider journal context", include_ai_task=False, data=old_connection, conversation_options={"chat_model": "o3-pro", "api_mode": "responses", "reasoning_effort": "high"})
    await setup(hass, entry)
    subentry = _conversation_subentry(entry)
    expected = dict(entry.data)
    source = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    source["provider_connection"] = new_connection
    source["agent"]["config"] = {**subentry.data, "reasoning_effort": None}
    target_config = deepcopy(source["agent"]["config"])
    artifact = backup.finalize_backup_snapshot(source)["document"]
    persist = restore_recovery._async_persist_config_entries
    writes = 0
    async def interrupted_persist(*args):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("configuration publication interrupted")
        await persist(*args)
    if interrupted:
        monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", interrupted_persist)
        with pytest.raises(backup.BackupError, match="completion is pending"):
            await transfer.async_restore_transfer(hass, entry, subentry, artifact, sections=[transfer.SECTION_CONFIGURATION], provider_connection=new_connection, expected_provider_data=expected)
        journal = await restore_recovery._journal_store(hass, entry.entry_id, subentry.subentry_id).async_load()
        assert journal["phase"] == "committed"
        assert journal["configuration_providers"] == {"target": "openai", "rollback": "azure"}
        assert dict(entry.data) == expected
        assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        # Parent credentials remain a separately confirmed operation, even after replay.
        assert dict(entry.data) == expected
        restore_private_provider_connection(hass, entry, new_connection, expected, confirmed=True)
    else:
        result = await transfer.async_restore_transfer(hass, entry, subentry, artifact, sections=[transfer.SECTION_CONFIGURATION], provider_connection=new_connection, expected_provider_data=expected)
        assert result["provider_connection_restored"]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert dict(subentry.data) == target_config
    assert dict(entry.data) == new_connection
    assert await restore_recovery._journal_store(hass, entry.entry_id, subentry.subentry_id).async_load() is None
    assert await hass.config_entries.async_unload(entry.entry_id)
