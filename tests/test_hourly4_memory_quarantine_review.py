"""A journal recovery must not remain blocked on quarantined Persistent Memory."""

from dataclasses import asdict
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    knowledge,
    memory,
    restore_recovery,
    temporary_memory,
)
from tests.restore_participants import participants
from tests.test_restore_initialization_recovery import _prepare


@pytest.mark.parametrize("phase", ["applying", "committed"])
async def test_valid_journal_replaces_quarantined_memory(hass, monkeypatch, phase):
    entry, subentry, selected, journal, _ = await _prepare(
        hass, monkeypatch, phase, "knowledge"
    )
    await knowledge.HomeAssistantKnowledgeStorage(
        hass, entry.entry_id, subentry.subentry_id
    ).async_save({"sources": [asdict(record) for record in selected.knowledge]})
    storage = memory.HomeAssistantMemoryStorage(
        hass, entry.entry_id, subentry.subentry_id
    )
    await storage.async_save({"memories": []})
    path = Path(storage._store.path)
    path.write_text("Preserved bad JSON", encoding="utf-8")
    evidence = path.with_name(path.name + ".corrupt.review")
    path.rename(evidence)

    async def managers(hass, entry_id, subentry_id):
        return participants(
            memory=await memory.async_get_memory(hass, entry_id, subentry_id)
        )

    async def apply(managers, prepared):
        await managers.memory.async_replace_backup(prepared.memories)

    monkeypatch.setattr(restore_recovery, "_durable_managers", managers)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    fresh = memory.PersistentMemory(
        memory.HomeAssistantMemoryStorage(hass, entry.entry_id, subentry.subentry_id)
    )
    await fresh.async_initialize()
    assert (await fresh.async_backup_data())["memories"] == [
        asdict(record) for record in selected.memories
    ]
    assert evidence.read_text(encoding="utf-8") == "Preserved bad JSON"
    assert await journal.async_load() is None


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("category", ["temporary", "knowledge"])
async def test_existing_record_category_bootstrap_preserves_quarantine_evidence(
    hass, monkeypatch, phase, category
):
    entry, subentry, selected, journal, path = await _prepare(
        hass, monkeypatch, phase, category
    )
    path.write_bytes(b"{preserved invalid native category JSON")
    evidence = path.with_name(path.name + ".corrupt.review")
    path.rename(evidence)
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    if category == "temporary":
        fresh = temporary_memory.TemporaryMemory(
            temporary_memory._temporary_memory_store(
                hass, entry.entry_id, subentry.subentry_id
            )
        )
        field, expected = "records", selected.temporary_memories
    else:
        fresh = knowledge.KnowledgeLibrary(
            knowledge.HomeAssistantKnowledgeStorage(
                hass, entry.entry_id, subentry.subentry_id
            )
        )
        field, expected = "sources", selected.knowledge
    await fresh.async_initialize()
    assert (await fresh.async_backup_data())[field] == [asdict(item) for item in expected]
    assert evidence.read_bytes() == b"{preserved invalid native category JSON"
    assert await journal.async_load() is None
