"""Reproduced audit failures at stored-data and recovery boundaries."""

from dataclasses import asdict, replace
from copy import deepcopy

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import backup, memory, restore_recovery, transfer, usage
from custom_components.extended_openai_conversation_responses.agent_config import normalize_agent_config
from custom_components.extended_openai_conversation_responses.conversation_archive import _tokens as archive_tokens, _excerpt
from custom_components.extended_openai_conversation_responses.knowledge import _tokens as knowledge_tokens
from custom_components.extended_openai_conversation_responses.temporary_memory import TemporaryMemory
from tests.test_backup import _document
from tests.test_backup_quarantined_function_export import _broken_config
from tests.test_memory_v2 import FakeStorage
from tests.test_temporary_memory import Storage, stored_record
from tests.test_transfer_user_mapping import _prepared


@pytest.mark.parametrize("left,right", [("Budget €50", "Budget $50"), ("I use C++", "I use C#"), ("Age <18", "Age >18")])
async def test_symbol_distinct_facts_are_not_duplicates(left, right):
    manager = memory.PersistentMemory(FakeStorage())
    await manager.async_initialize()
    await manager.async_add("alice", left, "general", "explicit")
    assert manager._find_duplicate("alice", right) is None


async def test_harmless_pin_suffix_and_legacy_memory_mode():
    memory.validate_memory_privacy("Chopin is my favourite composer", automatic=False)
    assert normalize_agent_config({"functions": [], "memory_enabled": True, "memory_auto_create": True})["memory_mode"] == "automatic"


def test_private_journal_preserves_literal_redaction_text():
    prepared = backup.inspect_backup(_document(), "agent-new")
    prepared.config["prompt"] = "Say [redacted] literally"
    journal = restore_recovery._new_journal("entry", "agent-new", prepared, prepared)
    _, target, rollback = restore_recovery._load_journal(journal, "entry", "agent-new")
    assert target.config["prompt"] == rollback.config["prompt"] == "Say [redacted] literally"


def test_quarantined_sibling_does_not_expose_credentials():
    config = _broken_config()
    tools = yaml.safe_load(config["functions"])
    tools[0]["function"] = {"type": "rest", "resource": "https://example.test", "headers": {"Authorization": "Bearer private-audit-token"}}
    config["functions"] = yaml.safe_dump(tools)
    exported = backup._safe_configuration(backup.export_configuration_snapshot(config))
    assert "private-audit-token" not in repr(exported)
    assert "reminders.unavailable" in repr(exported)


def test_owner_mapping_rejects_canonical_key_collision():
    prepared = _prepared()
    original = replace(prepared.memories[0], key="beverage.preferred")
    prepared.memories = [original, replace(original, memory_id="second", user_id="source-b")]
    with pytest.raises(backup.BackupError, match="duplicate canonical key"):
        transfer.apply_user_scope_mappings(prepared, {transfer.SECTION_PERSISTENT_MEMORY}, {"source-a": "dest", "source-b": "dest"})


def test_bare_voice_mapping_is_discovered():
    prepared = _prepared()
    prepared.config["voice_device_mappings"]["bare"] = "bare-owner"
    assert "bare-owner" in transfer.transfer_user_scope_ids(prepared, {transfer.SECTION_CONFIGURATION})


@pytest.mark.parametrize("token", ["北京", "東京"])
def test_unspaced_terms_and_reordered_excerpt(token):
    assert archive_tokens(token) <= archive_tokens("我住在" + token)
    assert knowledge_tokens(token) <= knowledge_tokens("我住在" + token)
    assert "bicycle" in _excerpt("irrelevant " * 100 + "My bicycle is red", "red bicycle")


async def test_automatic_noop_preserves_manual_provenance():
    raw = {**stored_record(0), "source": "manual"}
    store = Storage({"records": [raw]})
    manager = TemporaryMemory(store)
    await manager.async_initialize()
    before = deepcopy(store.data)
    updated = await manager.async_update(raw["scope_id"], raw["memory_id"], None, None, None, source="automatic", owner_scope_id="user:alice")
    assert updated.source == "manual"
    assert store.data == before


def test_memory_import_checks_content_privacy():
    prepared = _prepared()
    record = replace(prepared.memories[0], content="Password is hunter2")
    with pytest.raises(ValueError):
        memory.PersistentMemory.validate_backup_data({"memories": [asdict(record)]})
    with pytest.raises(ValueError):
        TemporaryMemory.validate_backup_data({"records": [{**stored_record(0), "content": "Password is hunter2"}]})


async def test_volatile_usage_refuses_incomplete_backup():
    manager = usage.UsageManager(usage._VolatileUsageStorage())
    await manager.async_initialize()
    with pytest.raises(RuntimeError, match="complete backup"):
        await manager.async_backup_data()


async def test_clear_keeps_active_private_boundary_and_rejects_private_turns():
    from custom_components.extended_openai_conversation_responses.conversation_archive import ConversationArchive, ArchiveTurn, _validated_session
    from custom_components.extended_openai_conversation_responses.scope import user_scope
    from tests.test_conversation_archive import FakeArchiveStorage

    archive = ConversationArchive(FakeArchiveStorage(), "agent")
    await archive.async_initialize()
    session = await archive.async_begin_session("key", user_scope("alice", source="test"), "conversation", archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    await archive.async_make_private(session.session_id)
    await archive.async_clear_scope(session.scope_id, confirm=True)
    private = archive.active_session("key")
    assert private.retention_state == "private"
    old = replace(private, last_activity_at="2020-01-01T00:00:00+00:00")
    assert _validated_session(asdict(old)).session_id == private.session_id
    turn = ArchiveTurn("turn", private.session_id, None, private.started_at, "secret", "reply", True)
    with pytest.raises(ValueError, match="private"):
        ConversationArchive.validate_backup_data({"sessions": [asdict(replace(private, turn_count=1))], "turns": [asdict(turn)]}, "agent")


async def test_unloaded_agent_deletes_only_its_persisted_delayed_calls(hass):
    from custom_components.extended_openai_conversation_responses.delayed_tools import DelayedToolManager, async_remove_stored_agent_calls

    manager = DelayedToolManager(hass)
    raw = {"calls": [{"entry_id": "entry", "subentry_id": "agent", "arguments": {"private": "secret"}}, {"entry_id": "entry", "subentry_id": "other"}]}
    await manager._store.async_save(raw)
    await async_remove_stored_agent_calls(hass, "entry", "agent")
    assert await manager._store.async_load() == {"calls": [raw["calls"][1]]}
