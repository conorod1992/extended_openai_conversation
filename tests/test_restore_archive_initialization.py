"""Full-agent journal authority can replace corrupted Archive transaction intent."""

from dataclasses import asdict
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    conversation_archive,
    knowledge,
    restore_recovery,
    temporary_memory,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from tests.restore_participants import participants
from tests.test_conversation_archive import _session, _turn
from tests.test_restore_initialization_recovery import _prepare


async def _archive_recovery(hass, monkeypatch, phase):
    entry, subentry, selected, journal, _ = await _prepare(
        hass, monkeypatch, phase, "knowledge"
    )
    await knowledge.HomeAssistantKnowledgeStorage(
        hass, entry.entry_id, subentry.subentry_id
    ).async_save({"sources": [asdict(record) for record in selected.knowledge]})
    data = await journal.async_load()
    for key in ["target", "rollback"]:
        data[key]["archive"] = {
            "sessions": [asdict(_session())],
            "turns": [asdict(_turn(user_text=f"{key} archive evidence"))],
            "active": {"browser": "session-1"},
        }
    await journal.async_save(data)
    storage = conversation_archive.HomeAssistantArchiveStorage(
        hass, entry.entry_id, subentry.subentry_id
    )
    await storage.async_save_partition(
        "2026-08",
        {
            "turns": [
                asdict(
                    _turn(
                        timestamp="2026-08-01T10:00:00+00:00",
                        user_text="Old unselected content",
                    )
                )
            ]
        },
    )
    await storage.async_save_metadata(
        {
            "sessions": [asdict(_session())],
            "partitions": ["2026-08"],
            "pending_partitions": {
                "2026-09": {"turns": [{"recoverable": "corrupt local intent"}]}
            },
        }
    )

    async def managers(hass, entry_id, subentry_id):
        return participants(
            temporary=await temporary_memory.async_get_temporary_memory(
                hass, entry_id, subentry_id
            ),
            knowledge=await knowledge.async_get_knowledge(hass, entry_id, subentry_id),
            archive=await conversation_archive.async_get_archive(
                hass, entry_id, subentry_id
            ),
        )

    async def apply(managers, prepared):
        await managers.temporary.async_replace_backup(prepared.temporary_memories)
        await managers.knowledge.async_replace_backup(prepared.knowledge)
        await managers.archive.async_replace_backup(
            prepared.archive_sessions,
            prepared.archive_turns,
            active=prepared.archive_active,
        )

    monkeypatch.setattr(restore_recovery, "_durable_managers", managers)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    return entry, subentry, storage, journal


@pytest.mark.parametrize("phase", ["applying", "committed"])
async def test_full_journal_recovers_corrupt_archive_intent_and_clears_old_indexed_content(
    hass, monkeypatch, phase
):
    entry, subentry, storage, journal = await _archive_recovery(
        hass, monkeypatch, phase
    )
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    manager = await conversation_archive.async_get_archive(
        hass, entry.entry_id, subentry.subentry_id
    )
    selected = "target" if phase == "committed" else "rollback"
    assert (await manager.async_backup_data())["turns"][0][
        "user_text"
    ] == f"{selected} archive evidence"
    assert manager.active_session("browser").session_id == "session-1"
    assert await storage.async_load_partition("2026-08") == {"turns": []}
    assert "pending_partitions" not in await storage.async_load_metadata()
    assert await journal.async_load() is None


@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("reached_disk", [False, True])
async def test_failed_recovery_staging_retains_full_journal_for_idempotent_retry(
    hass, monkeypatch, phase, reached_disk
):
    entry, subentry, storage, journal = await _archive_recovery(
        hass, monkeypatch, phase
    )
    path = Path(storage._metadata.path)
    original = path.read_bytes()
    save = conversation_archive.HomeAssistantArchiveStorage.async_save_metadata

    async def fail(storage, payload):
        if reached_disk:
            await save(storage, payload)
        raise OSError("metadata acknowledgement failed")

    with monkeypatch.context() as fault:
        fault.setattr(
            conversation_archive.HomeAssistantArchiveStorage,
            "async_save_metadata",
            fail,
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


async def test_archive_recovery_staging_requires_owned_quarantine(hass):
    storage = conversation_archive.HomeAssistantArchiveStorage(hass, "entry", "agent")
    with pytest.raises(RuntimeError, match="exclusive quarantined maintenance"):
        await storage.async_stage_journal_recovery([], [], None)
    assert not Path(storage._metadata.path).exists()
