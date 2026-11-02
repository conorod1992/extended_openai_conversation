"""A validated full-agent journal can replace rejected native Archive metadata."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    conversation_archive,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from tests.test_conversation_archive import _session, _turn
from tests.test_restore_archive_initialization import _archive_recovery


@pytest.mark.parametrize("phase", ["applying", "committed"])
async def test_valid_full_journal_recovers_unreadable_archive_metadata(
    hass, monkeypatch, phase
):
    entry, subentry, storage, journal = await _archive_recovery(
        hass, monkeypatch, phase
    )
    path = Path(storage._metadata.path)
    path.write_text("{unreadable native metadata", encoding="utf-8")
    # HA's native loader preserves rejected JSON under this naming convention.
    # Seed that post-quarantine state directly: its timestamp contains colons on
    # POSIX, which Windows cannot create, but the EOAI evidence rule is identical.
    evidence = path.with_name(path.name + ".corrupt.review")
    path.rename(evidence)
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    fresh = conversation_archive.ConversationArchive(
        conversation_archive.HomeAssistantArchiveStorage(
            hass, entry.entry_id, subentry.subentry_id
        ),
        subentry.subentry_id,
    )
    await fresh.async_initialize()
    expected = "target" if phase == "committed" else "rollback"
    assert (await fresh.async_backup_data())["turns"][0][
        "user_text"
    ] == f"{expected} archive evidence"
    assert await journal.async_load() is None
    assert evidence.read_text(encoding="utf-8") == "{unreadable native metadata"
    assert await storage.async_load_partition("2026-08") == {"turns": []}


def _quarantine(storage):
    path = Path(storage._metadata.path)
    path.write_text("Preserved metadata evidence", encoding="utf-8")
    evidence = path.with_name(path.name + ".corrupt.review")
    path.rename(evidence)
    return path, evidence


async def test_recovery_inventory_excludes_neighbors_suffixes_and_opaque_evidence(
    hass, monkeypatch
):
    entry, subentry, storage, journal = await _archive_recovery(
        hass, monkeypatch, "committed"
    )
    path, evidence = _quarantine(storage)
    stem = storage._prefix + ".turns."
    retained = {
        storage._prefix + "-neighbor.turns.2026-07": "Neighbor text",
        stem + "2026-07.extra": "Unknown suffix text",
        stem + "2026-07.corrupt.review": "Corrupt partition evidence",
        stem + "2026-13": "Invalid month evidence",
    }
    for name, value in retained.items():
        (path.parent / name).write_text(value, encoding="utf-8")
    opaque_directory = path.parent / (stem + "2026-06")
    opaque_directory.mkdir()
    (opaque_directory / "keep").write_text(
        "Opaque directory evidence", encoding="utf-8"
    )
    await storage.async_save_partition(
        "2026-07", {"turns": [{"old": "Superseded owned content"}]}
    )
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await storage.async_load_partition("2026-07") == {"turns": []}
    assert await storage.async_load_partition("2026-08") == {"turns": []}
    assert {
        name: (path.parent / name).read_text(encoding="utf-8") for name in retained
    } == retained
    assert (opaque_directory / "keep").read_text(
        encoding="utf-8"
    ) == "Opaque directory evidence"
    assert evidence.read_text(encoding="utf-8") == "Preserved metadata evidence"
    assert await journal.async_load() is None


@pytest.mark.parametrize("failure", ["inventory", "before_intent", "after_intent"])
async def test_failed_recovery_keeps_full_journal_and_evidence_for_idempotent_retry(
    hass, monkeypatch, failure
):
    entry, subentry, storage, journal = await _archive_recovery(
        hass, monkeypatch, "committed"
    )
    path, evidence = _quarantine(storage)
    old_partition = Path(storage._partition_store("2026-08").path).read_bytes()
    save = conversation_archive.HomeAssistantArchiveStorage.async_save_metadata
    with monkeypatch.context() as fault:
        if failure == "inventory":
            fault.setattr(
                conversation_archive,
                "_stored_archive_partitions",
                Mock(side_effect=OSError("Directory unreadable")),
            )
        else:

            async def fail(writer, payload):
                if failure == "after_intent":
                    await save(writer, payload)
                raise OSError("Metadata intent acknowledgement failed")

            fault.setattr(
                conversation_archive.HomeAssistantArchiveStorage,
                "async_save_metadata",
                fail,
            )
        with pytest.raises(backup.BackupError, match="could not be recovered"):
            await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert path.exists() is (failure == "after_intent")
    assert evidence.read_text(encoding="utf-8") == "Preserved metadata evidence"
    assert Path(storage._partition_store("2026-08").path).read_bytes() == old_partition
    assert await journal.async_load() is not None
    assert await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert await storage.async_load_partition("2026-08") == {"turns": []}
    assert await journal.async_load() is None


@pytest.mark.parametrize(
    "failure",
    [
        OSError("Native read failed"),
        RuntimeError("Programming failure"),
        asyncio.CancelledError(),
    ],
)
async def test_metadata_read_errors_cannot_be_treated_as_quarantined_source(
    hass, monkeypatch, failure
):
    storage = conversation_archive.HomeAssistantArchiveStorage(
        hass, "read-entry", "read-agent"
    )
    load = AsyncMock(side_effect=failure)
    save = AsyncMock()
    inventory = Mock()
    monkeypatch.setattr(storage, "async_load_metadata", load)
    monkeypatch.setattr(storage, "async_save_metadata", save)
    monkeypatch.setattr(conversation_archive, "_stored_archive_partitions", inventory)
    gate = get_agent_maintenance_gate(hass, "read-entry", "read-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        try:
            with gate.recovery_work(), pytest.raises(type(failure)):
                await storage.async_stage_journal_recovery([_session()], [_turn()], {})
        finally:
            gate.recovery_required = False
    load.assert_awaited_once()
    inventory.assert_not_called()
    save.assert_not_awaited()


async def test_invalid_recovery_candidate_is_rejected_before_metadata_read_or_inventory(
    hass, monkeypatch
):
    storage = conversation_archive.HomeAssistantArchiveStorage(
        hass, "validate-entry", "validate-agent"
    )
    load = AsyncMock()
    save = AsyncMock()
    inventory = Mock()
    monkeypatch.setattr(storage, "async_load_metadata", load)
    monkeypatch.setattr(storage, "async_save_metadata", save)
    monkeypatch.setattr(conversation_archive, "_stored_archive_partitions", inventory)
    gate = get_agent_maintenance_gate(hass, "validate-entry", "validate-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        try:
            with gate.recovery_work(), pytest.raises(ValueError):
                await storage.async_stage_journal_recovery(
                    [_session()], [replace(_turn(), sequence=True)], {}
                )
        finally:
            gate.recovery_required = False
    load.assert_not_awaited()
    inventory.assert_not_called()
    save.assert_not_awaited()
