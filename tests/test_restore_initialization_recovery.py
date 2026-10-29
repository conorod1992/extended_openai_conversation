"""Validated full-agent journals recover rejected destination generations."""

from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    knowledge,
    restore_recovery,
    temporary_memory,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from tests.restore_participants import participants
from tests.test_restore_recovery import _identity, _states


async def _prepare(hass, monkeypatch, phase, damaged):
    entry, subentry = _identity()
    target, rollback = _states()
    target.knowledge = [
        replace(target.knowledge[0], content="Target recovery evidence")
    ]
    rollback.knowledge = [
        replace(rollback.knowledge[0], content="Rollback recovery evidence")
    ]
    target.temporary_memories = [
        replace(target.temporary_memories[0], content="Target reminder")
    ]
    rollback.temporary_memories = [
        replace(rollback.temporary_memories[0], content="Rollback reminder")
    ]
    selected = target if phase == "committed" else rollback
    journal = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, target, rollback
    )
    journal["phase"] = phase
    journal_store = restore_recovery._journal_store(
        hass, entry.entry_id, subentry.subentry_id
    )
    await journal_store.async_save(journal)
    storage = (
        temporary_memory._temporary_memory_store(
            hass, entry.entry_id, subentry.subentry_id
        )
        if damaged.startswith("temporary")
        else knowledge.HomeAssistantKnowledgeStorage(
            hass, entry.entry_id, subentry.subentry_id
        )
    )
    payload = {
        "records" if damaged.startswith("temporary") else "sources": {
            "recoverable": "damaged container"
        }
    }
    if damaged == "temporary_record":
        payload = {"records": [None]}
    await storage.async_save(payload)
    native = getattr(storage, "_store", storage)
    path = Path(native.path)

    async def managers(hass, entry_id, subentry_id):
        temporary = await temporary_memory.async_get_temporary_memory(
            hass, entry_id, subentry_id
        )
        library = await knowledge.async_get_knowledge(hass, entry_id, subentry_id)
        return participants(temporary=temporary, knowledge=library)

    async def apply(managers, prepared):
        await managers.temporary.async_replace_backup(prepared.temporary_memories)
        await managers.knowledge.async_replace_backup(prepared.knowledge)

    monkeypatch.setattr(restore_recovery, "_durable_managers", managers)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", Mock())
    monkeypatch.setattr(restore_recovery, "_update_configuration", AsyncMock())
    return entry, subentry, selected, journal_store, path


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("damaged", ["temporary", "temporary_record", "knowledge"])
async def test_journal_recovers_malformed_destination_before_normal_initialization(
    hass, monkeypatch, phase, damaged
):
    entry, subentry, selected, journal, _ = await _prepare(
        hass, monkeypatch, phase, damaged
    )
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is None
    library = await knowledge.async_get_knowledge(
        hass, entry.entry_id, subentry.subentry_id
    )
    temporary = await temporary_memory.async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    assert (await library.async_backup_data())["sources"] == [
        asdict(record) for record in selected.knowledge
    ]
    assert (await temporary.async_backup_data())["records"] == [
        asdict(record) for record in selected.temporary_memories
    ]
    assert not await restore_recovery.async_recover_pending_restore(
        hass, entry, subentry
    )
    # Ordinary changes are admitted after authoritative recovery releases quarantine.
    await library.async_create("New reference", "", "Fresh knowledge")


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("reached_disk", [False, True])
async def test_repair_write_failure_keeps_journal_and_quarantine_for_retry(
    hass, monkeypatch, phase, reached_disk
):
    entry, subentry, _, journal, path = await _prepare(
        hass, monkeypatch, phase, "knowledge"
    )
    original = path.read_bytes()
    save = knowledge.HomeAssistantKnowledgeStorage.async_save

    async def failed_save(storage, payload):
        if reached_disk:
            await save(storage, payload)
        raise OSError("write failed")

    with monkeypatch.context() as fault:
        fault.setattr(
            knowledge.HomeAssistantKnowledgeStorage,
            "async_save",
            failed_save,
        )
        with pytest.raises(backup.BackupError, match="could not be recovered"):
            await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert (path.read_bytes() != original) is reached_disk
    assert await journal.async_load() is not None
    assert get_agent_maintenance_gate(
        hass, entry.entry_id, subentry.subentry_id
    ).recovery_required
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await journal.async_load() is None


async def test_recovery_fallback_requires_exclusive_ownership(hass):
    entry, subentry = _identity()
    target, _ = _states()
    with pytest.raises(RuntimeError, match="exclusive maintenance"):
        await restore_recovery._recovery_managers(
            hass, entry.entry_id, subentry.subentry_id, target
        )


async def test_invalid_journal_cannot_authorize_destination_repair(hass, monkeypatch):
    entry, subentry, _, journal, path = await _prepare(
        hass, monkeypatch, "committed", "knowledge"
    )
    original = path.read_bytes()
    data = await journal.async_load()
    data["entry_id"] = "another-entry"
    await journal.async_save(data)
    with pytest.raises(backup.BackupError, match="corrupted"):
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert path.read_bytes() == original
    assert await journal.async_load() == data


@pytest.mark.parametrize("error_type", [ValueError, OSError])
async def test_unrelated_startup_failure_cannot_overwrite_healthy_stores(
    hass, monkeypatch, error_type
):
    entry, subentry = _identity()
    target, rollback = _states()
    journal = restore_recovery._journal_store(
        hass, entry.entry_id, subentry.subentry_id
    )
    await journal.async_save(
        restore_recovery._new_journal(
            entry.entry_id, subentry.subentry_id, target, rollback
        )
    )
    monkeypatch.setattr(
        restore_recovery,
        "_durable_managers",
        AsyncMock(side_effect=error_type("unrelated startup failure")),
    )
    temporary_save = AsyncMock()
    knowledge_save = AsyncMock()
    monkeypatch.setattr(
        temporary_memory.TemporaryMemoryStore, "async_save", temporary_save
    )
    monkeypatch.setattr(
        knowledge.HomeAssistantKnowledgeStorage, "async_save", knowledge_save
    )
    with pytest.raises(backup.BackupError, match="could not be recovered"):
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    temporary_save.assert_not_called()
    knowledge_save.assert_not_called()
    assert await journal.async_load() is not None
