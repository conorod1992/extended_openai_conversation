"""Configuration and restore transitions retain failed-continuation I/O ownership."""

from copy import deepcopy
from dataclasses import replace

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    function_groups,
    management_ui,
    restore_recovery,
    services,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
    validate_function_groups,
)
from custom_components.extended_openai_conversation_responses.entity import (
    _format_tools,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
)
from custom_components.extended_openai_conversation_responses.management_function_repair import (
    repair_revision,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    bind_effect_replay_scope,
    remember_unacknowledged_calls,
    replay_safety,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_backup import FakeStorage
from tests.test_conversation_archive import FakeArchiveStorage
from tests.test_function_groups_mutation import _group
from tests.test_function_mutation_ownership import request
from tests.test_function_tool_error_recovery import _call, _chat_log, _retain_calls
from tests.test_management_loading_performance import _hass_with_agent
from tests.test_receipt_ownership_contracts import http_boundary as http_boundary
from tests.test_restore_recovery import MemoryJournalStore
from tests.test_review_replay_execution import _entity, _execute


def _provider_names(subentry):
    """Use production group assembly and the provider's actual schema formatter."""
    tools = configured_function_tools_from_data(subentry.data)
    groups = validate_function_groups(subentry.data["function_groups"], tools)
    assembly = function_groups.assemble_function_tools(tools, groups, set())
    return [item["name"] for item in _format_tools(assembly.tools, "responses")]


@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
async def test_prepared_http_receipt_survives_public_edits_and_restore(
    hass, monkeypatch, http_boundary, rollback
):
    await _assert_edits_restore_keep_receipts(
        hass, monkeypatch, http_boundary, rollback
    )


async def _assert_edits_restore_keep_receipts(
    hass, monkeypatch, http_boundary, rollback
):
    """A retained failed continuation cannot redispatch after schema/runtime changes.

    The retry deliberately keeps its original ChatLog identity. A new logical
    conversation has its own replay scope and is outside this contract.
    """
    _local, entry, subentry = _hass_with_agent()
    hass.config_entries = _local.config_entries
    tool = {
        "spec": {
            "name": "dispense",
            "description": "Dispense one item",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "rest",
            "method": "POST",
            "resource": http_boundary.resource,
            "payload": "1",
        },
    }
    subentry.data = {
        "functions": yaml.safe_dump([tool]),
        "function_groups": [_group("effects", ["dispense"], "always")],
        "extension_data": {"retain": [False, None]},
    }
    agent = (hass, entry, subentry)
    entity, log = _entity(hass), _chat_log(hass)
    entity.entry, entity.subentry = entry, subentry
    group_runtime = function_groups.reset_function_group_runtime(
        hass, entry.entry_id, subentry.subentry_id
    )
    entity._function_groups_runtime = group_runtime
    group_runtime.begin("conversation:before-restore", 30)

    memory_store = FakeStorage()
    guest = GuestModeManager(hass, entry.entry_id, subentry.subentry_id)
    guest._store = FakeStorage()
    participants = backup.AgentDataParticipants(
        memory=backup.PersistentMemory(memory_store),
        temporary=backup.TemporaryMemory(FakeStorage()),
        knowledge=backup.KnowledgeLibrary(FakeStorage()),
        archive=backup.ConversationArchive(FakeArchiveStorage(), subentry.subentry_id),
        usage=backup.UsageManager(FakeStorage(), FakeStorage(), FakeStorage()),
        guest_mode=guest,
        request_rules=backup.RequestRules(FakeStorage()),
    )
    for manager in (
        participants.memory,
        participants.temporary,
        participants.knowledge,
        participants.archive,
        participants.usage,
        participants.guest_mode,
        participants.request_rules,
    ):
        await manager.async_initialize()
    await participants.memory.async_add(
        "alice", "Original retained preference", "general", "explicit"
    )

    async def rules(*_args):
        return participants.request_rules

    monkeypatch.setattr(management_ui, "async_get_request_rules", rules)
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda *_args: (entry.entry_id, subentry.subentry_id),
    )
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: entity)
    assert _provider_names(subentry) == ["dispense"]
    first = _call("first", "dispense")
    _retain_calls(log, [first])
    with bind_effect_replay_scope(entity, log.conversation_id):
        await _execute(
            entity, configured_function_tools_from_data(subentry.data)[0], first
        )
    assert http_boundary.received == [b"1"]
    # This is the same production retention boundary used after provider failure.
    remember_unacknowledged_calls(entity, log, set())

    await services.async_set_function_groups_enabled(
        hass, entry.entry_id, subentry.subentry_id, ["effects"], False
    )
    assert _provider_names(subentry) == []
    renamed = deepcopy(tool)
    renamed["spec"]["name"] = "dispense_again"
    await management_ui.async_tools_command(
        request(
            agent,
            "save",
            tool=renamed,
            original_name="dispense",
            revision=repair_revision(subentry),
        )
    )
    group = deepcopy(subentry.data["function_groups"][0])
    group["enabled"] = True
    await management_ui.async_tools_command(
        request(
            agent,
            "save_group",
            group=group,
            original_id="effects",
            revision=repair_revision(subentry),
        )
    )
    assert _provider_names(subentry) == ["dispense_again"]

    target = await backup._snapshot_for_restore(participants, subentry)
    target.memories = [
        replace(target.memories[0], content="Restored retained preference")
    ]
    journal = MemoryJournalStore()

    async def durable(*_args):
        return participants

    async def verify_config(_hass, _entry_id, _subentry_id, prepared):
        assert subentry.data == prepared.config and subentry.title == prepared.title

    monkeypatch.setattr(restore_recovery, "_durable_managers", durable)
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: journal)
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", verify_config
    )
    if rollback:
        replace_knowledge = participants.knowledge.async_replace_backup
        failed = False

        async def fail_once(sources):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("Knowledge write rejected after Memory replacement")
            await replace_knowledge(sources)

        monkeypatch.setattr(participants.knowledge, "async_replace_backup", fail_once)
        with pytest.raises(
            backup.BackupError, match="previous agent state was recovered"
        ):
            await backup.async_restore_backup(hass, entry, subentry, target)
    else:
        result = await backup.async_restore_backup(hass, entry, subentry, target)
        assert result["status"] == "restored"
    assert journal.data is None and journal.removed == 1
    assert group_runtime.stats()["active_function_group_sessions"] == 0
    assert entity._function_groups_runtime is group_runtime
    reloaded = backup.PersistentMemory(memory_store)
    await reloaded.async_initialize()
    saved = await reloaded.async_backup_data()
    assert saved["memories"][0]["content"] == (
        "Original retained preference" if rollback else "Restored retained preference"
    )
    assert _provider_names(subentry) == ["dispense_again"]
    assert subentry.data["extension_data"] == {"retain": [False, None]}

    retry = _call("retry", "dispense_again")
    _retain_calls(log, [retry])
    blocked = False
    with bind_effect_replay_scope(entity, log.conversation_id):
        try:
            await _execute(
                entity, configured_function_tools_from_data(subentry.data)[0], retry
            )
        except HomeAssistantError as err:
            assert "not acknowledged" in str(err)
            blocked = True
    assert http_boundary.received == [b"1"], (
        "restore and schema edits must not erase failed-continuation effect ownership"
    )
    assert blocked


async def test_cross_stage_receipt_oracle_detects_runtime_reset_mutation(
    hass, monkeypatch, http_boundary
):
    reset = restore_recovery.reset_restored_runtime

    def lose_failed_continuation(*args, **kwargs):
        reset(*args, **kwargs)
        entity = restore_recovery._active_agent(*args[:3])
        replay_safety(entity).effects.clear()

    monkeypatch.setattr(
        restore_recovery, "reset_restored_runtime", lose_failed_continuation
    )
    with pytest.raises(AssertionError, match="must not erase failed-continuation"):
        await _assert_edits_restore_keep_receipts(
            hass, monkeypatch, http_boundary, False
        )
    assert http_boundary.received == [b"1", b"1"]
