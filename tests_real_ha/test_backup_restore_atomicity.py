"""Real Home Assistant regression coverage for atomic backup restore failure."""

from __future__ import annotations

import asyncio
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
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


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



@pytest.mark.parametrize("interrupted", [False, True, "deleted"])
@pytest.mark.usefixtures("real_store_io")
async def test_cross_provider_private_restore_and_committed_replay(hass, monkeypatch, interrupted):
    from custom_components.extended_openai_conversation_responses import restore_recovery, transfer
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry as setup
    from homeassistant.config_entries import ConfigSubentry
    old_connection = {"api_provider": "azure", "base_url": "https://example.openai.azure.com", "api_version": "2025-04-01-preview", "api_key": "test-azure", "skip_authentication": True}
    new_connection = {"api_provider": "openai", "api_key": "test-openai", "skip_authentication": True}
    entry = _make_entry("Provider journal context", include_ai_task=False, data=old_connection, conversation_options={"chat_model": "o3-pro", "api_mode": "responses", "reasoning_effort": "high"})
    await setup(hass, entry)
    subentry = _conversation_subentry(entry)
    if interrupted == "deleted":
        sibling = ConfigSubentry(
            data=dict(subentry.data),
            subentry_type="conversation",
            title="Surviving conversation",
            unique_id=None,
        )
        hass.config_entries.async_add_subentry(entry, sibling)
        await hass.async_block_till_done(wait_background_tasks=True)
    expected = dict(entry.data)
    source = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    source["provider_connection"] = new_connection
    source["agent"]["config"] = {**subentry.data, "reasoning_effort": None}
    target_config = deepcopy(source["agent"]["config"])
    artifact = backup.finalize_backup_snapshot(source)["document"]
    persist = restore_recovery.async_persist_config_entries
    writes = 0
    async def interrupted_persist(*args):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("configuration publication interrupted")
        await persist(*args)
    if interrupted:
        monkeypatch.setattr(restore_recovery, "async_persist_config_entries", interrupted_persist)
        with pytest.raises(backup.BackupError, match="completion is pending"):
            await transfer.async_restore_transfer(hass, entry, subentry, artifact, sections=[transfer.SECTION_CONFIGURATION], provider_connection=new_connection, expected_provider_data=expected)
        journal = await restore_recovery._journal_store(hass, entry.entry_id, subentry.subentry_id).async_load()
        assert journal["phase"] == "committed"
        assert journal["configuration_providers"] == {"target": "openai", "rollback": "azure"}
        assert dict(entry.data) == new_connection
        assert str(entry.runtime_data.base_url).startswith(old_connection["base_url"])
        from custom_components.extended_openai_conversation_responses.provider_credentials import (
            require_provider_restore_complete,
        )
        from homeassistant.exceptions import HomeAssistantError

        with pytest.raises(HomeAssistantError, match="shared provider connection is unavailable"):
            require_provider_restore_complete(hass, entry.entry_id)
        if interrupted == "deleted":
            assert hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
            await hass.async_block_till_done(wait_background_tasks=True)
            require_provider_restore_complete(hass, entry.entry_id)
            assert set(entry.subentries) == {sibling.subentry_id}
            assert dict(entry.data) == new_connection
            assert str(entry.runtime_data.base_url).startswith("https://api.openai.com/")
            assert await restore_recovery._journal_store(
                hass, entry.entry_id, subentry.subentry_id
            ).async_load() is None
            assert await hass.config_entries.async_unload(entry.entry_id)
            return
        assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        require_provider_restore_complete(hass, entry.entry_id)
    else:
        result = await transfer.async_restore_transfer(hass, entry, subentry, artifact, sections=[transfer.SECTION_CONFIGURATION], provider_connection=new_connection, expected_provider_data=expected)
        assert result["provider_connection_restored"]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert dict(subentry.data) == target_config
    assert dict(entry.data) == new_connection
    assert str(entry.runtime_data.base_url).startswith("https://api.openai.com/")
    assert await restore_recovery._journal_store(hass, entry.entry_id, subentry.subentry_id).async_load() is None
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("failed_writes", [1, 2], ids=["first-reload", "ha-retry"])
@pytest.mark.usefixtures("real_store_io")
async def test_provider_restore_reloads_assistants_after_committed_write_failure(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, failed_writes: int
) -> None:
    """Recover the shared credentials before loading any assistant on that entry."""
    from homeassistant.components import conversation
    from homeassistant.core import Context, callback
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.extended_openai_conversation_responses import (
        restore_recovery,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.const import (
        API_MODE_CHAT_COMPLETIONS,
        CONF_API_MODE,
        CONF_CHAT_MODEL,
    )
    from custom_components.extended_openai_conversation_responses.provider_credentials import (
        require_provider_restore_complete,
    )
    from tests_real_ha.test_acceptance_lifecycle import _make_entry, _subentry
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _install_wire,
        _raw_client,
    )

    config = {CONF_API_MODE: API_MODE_CHAT_COMPLETIONS, CONF_CHAT_MODEL: "gpt-4o-mini"}
    previous = {
        CONF_API_KEY: "previous-key",
        CONF_SKIP_AUTHENTICATION: True,
    }
    restored = {**previous, CONF_API_KEY: "restored-key"}
    shared = MockConfigEntry(
        domain=DOMAIN,
        title="Shared provider recovery",
        data=previous,
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            _subentry("conversation", title, config)
            for title in ("Restore target", "First sibling", "Second sibling")
        ],
    )
    other = _make_entry(
        "Independent provider", include_ai_task=False, conversation_options=config
    )
    await _setup_entry(hass, shared)
    await _setup_entry(hass, other)
    target, *siblings = shared.subentries.values()
    sibling_subentry_ids = {sibling.subentry_id for sibling in siblings}
    sibling_ids = [
        entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), shared.entry_id
        )
        if entity.domain == "conversation"
        and entity.config_subentry_id in sibling_subentry_ids
    ]
    assert len(sibling_ids) == 2

    async def say(agent_id: str) -> str:
        result = await conversation.async_converse(
            hass, "Give me the provider response", None, Context(), "en", agent_id
        )
        assert result.response.error_code is None
        return result.response.speech["plain"]["speech"]

    source = await backup.async_collect_backup_snapshot(hass, shared, target)
    source["provider_connection"] = restored
    artifact = backup.finalize_backup_snapshot(source)["document"]
    persist = restore_recovery.async_persist_config_entries
    writes = 0

    async def fail_writes(*args: Any) -> None:
        nonlocal writes
        writes += 1
        if writes <= failed_writes:
            raise OSError("injected configuration persistence failure")
        await persist(*args)

    monkeypatch.setattr(restore_recovery, "async_persist_config_entries", fail_writes)
    with pytest.raises(backup.BackupError, match="completion is pending"):
        await transfer.async_restore_transfer(
            hass,
            shared,
            target,
            artifact,
            sections=[transfer.SECTION_CONFIGURATION],
            provider_connection=restored,
            expected_provider_data=previous,
        )
    journal = restore_recovery._journal_store(hass, shared.entry_id, target.subentry_id)
    assert (await journal.async_load())["phase"] == "committed"
    with pytest.raises(HomeAssistantError, match="shared provider connection is unavailable"):
        require_provider_restore_complete(hass, shared.entry_id)

    other_agent = conversation.async_get_agent(hass, other.entry_id)
    unrelated_wire = _install_wire(
        monkeypatch, other_agent, [_chat_sse_text("Independent provider replied")]
    )
    assert await say(other_agent.entity_id) == "Independent provider replied"
    assert len(unrelated_wire.requests) == 1

    reloaded = await hass.config_entries.async_reload(shared.entry_id)
    if failed_writes == 2:
        assert not reloaded
        assert shared.state is ConfigEntryState.SETUP_RETRY
        assert (await journal.async_load())["phase"] == "committed"
        with pytest.raises(
            HomeAssistantError, match="shared provider connection is unavailable"
        ):
            require_provider_restore_complete(hass, shared.entry_id)
        loaded = asyncio.Event()

        @callback
        def on_state_change() -> None:
            if shared.state is ConfigEntryState.LOADED:
                loaded.set()

        remove_listener = shared.async_on_state_change(on_state_change)
        try:
            async_fire_time_changed(hass, fire_all=True)
            await asyncio.wait_for(loaded.wait(), timeout=10)
        finally:
            remove_listener()
    else:
        assert reloaded
    await hass.async_block_till_done()
    assert shared.state is ConfigEntryState.LOADED

    agent = conversation.async_get_agent(hass, sibling_ids[0])
    assert agent is not None
    assert _raw_client(agent).api_key == restored[CONF_API_KEY]
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("First reply"), _chat_sse_text("Second reply")],
    )
    assert [await say(agent_id) for agent_id in sibling_ids] == [
        "First reply",
        "Second reply",
    ]
    assert len(wire.requests) == 2
    assert writes == failed_writes + 1
    assert await journal.async_load() is None
    require_provider_restore_complete(hass, shared.entry_id)
    assert shared.entry_id not in hass.data.get(
        restore_recovery.RESTORE_RELOAD_PENDING, set()
    )


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.usefixtures("real_store_io")
async def test_knowledge_restore_ignores_damaged_usage_details(hass, monkeypatch, recover):
    from pathlib import Path

    from custom_components.extended_openai_conversation_responses import (
        restore_recovery, transfer, usage,
    )
    from tests_real_ha.test_usage_period_statistics import _unload_recorded_usage

    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    key = entry.entry_id, subentry.subentry_id
    accounting = await usage.async_get_usage(hass, *key)
    await accounting.async_record_request(successful=True, usage=usage.RequestUsage(total_tokens=85))
    await accounting.async_shutdown()
    document = backup.finalize_backup_snapshot(
        await backup.async_collect_backup_snapshot(hass, entry, subentry)
    )["document"]
    journal = restore_recovery._journal_store(hass, *key)
    _, _, detail = usage._durable_usage_stores(hass, *key)
    if recover:
        with monkeypatch.context() as faults:
            faults.setattr(
                restore_recovery, "_update_configuration",
                _raise_interrupted_usage_restore,
            )
            with pytest.raises(backup.BackupError, match="completion is pending"):
                await transfer.async_restore_transfer(
                    hass, entry, subentry, document, sections=[transfer.SECTION_KNOWLEDGE]
                )
        assert (await journal.async_load())["data_sections"] == ["knowledge"]
    await _unload_recorded_usage(hass, key, accounting)
    path = Path(detail.path)
    if recover:
        path.write_bytes(b"invalid Usage JSON")
        assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        assert path.read_bytes() == b"invalid Usage JSON"
    else:
        await detail.async_save({"requests": {"invalid": True}, "runs": []})
        restored = await transfer.async_restore_transfer(
            hass, entry, subentry, document, sections=[transfer.SECTION_KNOWLEDGE]
        )
        assert restored["status"] == "restored"
        assert await detail.async_load() == {"requests": {"invalid": True}, "runs": []}
    assert await journal.async_load() is None
    assert (await usage.async_get_usage(hass, *key)).totals.total_tokens == 85


async def _raise_interrupted_usage_restore(*args, **kwargs):
    raise OSError("configuration publication interrupted")


@pytest.mark.parametrize("case", ["same", "collision", "expired", "recovery"])
async def test_archive_restore_keeps_only_active_destination_privacy_markers(
    hass, monkeypatch, case
):
    from datetime import timedelta

    from custom_components.extended_openai_conversation_responses import (
        conversation_archive, restore_recovery, transfer,
    )
    from custom_components.extended_openai_conversation_responses.scope import user_scope

    entry = _entry()
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    owner = user_scope("test-owner", source="test")
    bound = "conversation:native-chat"
    archive = await conversation_archive.async_get_archive(hass, entry.entry_id, subentry.subentry_id)

    async def begin(manager, scope=owner):
        return await manager.async_begin_session(
            bound, scope, "native-chat", archive_enabled=True,
            shared_archive_enabled=False, inactivity_minutes=30,
        )

    first = await begin(archive)
    await archive.async_record_turn(
        first.session_id, run_id=None, user_text="Portable transcript", assistant_text="OK",
        successful=True,
    )
    opening = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    await archive.async_make_private(first.session_id)
    if case == "collision":
        # A portable import may reuse the exact archive ID for another owner.
        opening["agent"]["source_subentry_id"] = "another-assistant"
        opening["archive"]["sessions"][0]["scope_id"] = "user:different-owner"
    elif case == "same":
        opening = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    elif case == "expired":
        future = conversation_archive.dt_util.utcnow() + timedelta(minutes=31)
        monkeypatch.setattr(conversation_archive.dt_util, "utcnow", lambda: future)
    artifact = backup.finalize_backup_snapshot(opening)["document"]

    async def restore():
        await transfer.async_restore_transfer(
            hass, entry, subentry, artifact, sections=[transfer.SECTION_CONVERSATION_ARCHIVE],
        )

    if case == "recovery":
        async def interrupt(*_args, **_kwargs):
            raise OSError("interrupted after commit")

        with monkeypatch.context() as failed:
            failed.setattr(restore_recovery, "_update_configuration", interrupt)
            with pytest.raises(backup.BackupError, match="completion is pending"):
                await restore()
        assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    else:
        await restore()

    # Reload independently of the in-memory Archive and continuity managers.
    fresh = conversation_archive.ConversationArchive(
        conversation_archive.HomeAssistantArchiveStorage(hass, entry.entry_id, subentry.subentry_id),
        subentry.subentry_id,
    )
    await fresh.async_initialize()
    marker = fresh.active_session(bound)
    if case == "expired":
        assert marker is None
        assert (await begin(fresh)).retention_state == "retained"
    else:
        assert marker.scope_id == owner.scope_id
        assert marker.home_assistant_conversation_id == "native-chat"
        assert marker.retention_state == "private"
        assert marker.title == "" and marker.turn_count == 0
        assert (await begin(fresh)).session_id == marker.session_id
        assert await fresh.async_record_turn(
            marker.session_id, run_id=None, user_text="Must stay private", assistant_text="Hidden",
            successful=True,
        ) is None
        if case == "collision":
            assert marker.session_id != first.session_id
            assert (await fresh.async_backup_data())["turns"][0]["session_id"] == first.session_id
        resumed = await fresh.async_resume_saving(
            bound, marker.session_id, owner, shared_archive_enabled=False,
        )
        assert resumed.retention_state == "retained"
        assert resumed.session_id != marker.session_id
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("real_store_io")
async def test_private_backup_rejects_oversized_live_ha_catalogue(hass, monkeypatch):
    from copy import deepcopy

    from custom_components.extended_openai_conversation_responses import (
        backup,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
        async_discover,
        new_reference_tool,
    )
    from homeassistant.components import conversation
    from tests.test_ha_llm_tools import TestAPI, context, register
    from tests.test_runtime_readiness import _options, _tool
    from tests_real_ha.test_acceptance_lifecycle import (
        _conversation_subentry,
        _make_entry,
        _setup_entry,
    )
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_text,
        _install_wire,
        _say,
        _speech,
    )

    register(hass, TestAPI(hass))
    live = await async_discover(hass, context())
    ref = new_reference_tool(next(iter(live.tools.values())).reference, set())
    neutral = {
        "exposed_entities_enabled": False,
        "current_datetime_enabled": False,
        "prompt": "Plain prompt",
    }
    existing = _options(126, **neutral)
    functions = [*[_tool(f"marker_{i}") for i in range(127)], ref]
    incoming = _options(functions=functions, **neutral)
    entry = _make_entry(
        "Private restore limit", include_ai_task=False, conversation_options=existing
    )
    await _setup_entry(hass, entry)
    subentry = _conversation_subentry(entry)
    before = conversation.async_get_agent(hass, entry.entry_id)
    baseline_wire = _install_wire(
        monkeypatch, before, [_chat_sse_text("Baseline working")]
    )
    assert _speech(await _say(hass, before)) == "Baseline working"
    assert len(baseline_wire.requests) == 1
    source = await backup.async_collect_backup_snapshot(hass, entry, subentry)
    source["agent"]["config"] = deepcopy(incoming)
    artifact = backup.finalize_backup_snapshot(source)["document"]
    sections = [transfer.SECTION_CONFIGURATION]
    with pytest.raises(backup.BackupError, match="128-tool limit"):
        await transfer.async_materialize_restore(
            hass, entry, subentry, artifact, sections=sections
        )
    with pytest.raises(backup.BackupError, match="128-tool limit"):
        await transfer.async_restore_transfer(
            hass, entry, subentry, artifact, sections=sections
        )
    assert subentry.data["functions"] == existing["functions"]
    wire = _install_wire(monkeypatch, before, [_chat_sse_text("Still working")])
    assert _speech(await _say(hass, before)) == "Still working"
    assert len(wire.requests) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
