"""Selective planning and journaled commit share one maintenance transaction."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    backup,
    restore_recovery,
    transfer,
)
from tests.test_restore_recovery import FakeHass, MemoryJournalStore, _identity, _states


class RestoreHarness:
    """Use the real gate, planner, journal, apply order and rollback with RAM stores."""

    def __init__(self, monkeypatch) -> None:
        self.hass = FakeHass()
        self.entry, self.subentry = _identity()
        _, self.state = _states()
        self.subentry.data = deepcopy(self.state.config)
        self.subentry.title = self.state.title
        self.gate = agent_maintenance.get_agent_maintenance_gate(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        self.lock = backup._backup_lock(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        self.journal = MemoryJournalStore()
        self.pause_at = None
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.fail_knowledge_writes = 0
        self.snapshots = []
        self.writes = []
        self.imported = transfer.PreparedTransfer(
            source_kind="custom_backup",
            mode="custom",
            title="Imported",
            available_sections=frozenset({transfer.SECTION_KNOWLEDGE}),
            created_at=None,
            integration_version=None,
            knowledge=[],
        )

        def manager(*fields):
            async def replace_data(*values):
                # Store writes must always follow maintenance -> backup lock.
                assert self.gate._writer_active
                assert self.lock.locked()
                self.writes.append(fields)
                if fields == ("knowledge",) and self.fail_knowledge_writes:
                    self.fail_knowledge_writes -= 1
                    raise OSError("knowledge store unavailable")
                for field, value in zip(fields, values, strict=True):
                    setattr(self.state, field, deepcopy(value))
                await self.pause("apply")

            return SimpleNamespace(async_replace_backup=replace_data)

        self.managers = (
            manager("memories"),
            manager("temporary_memories"),
            manager("knowledge"),
            manager("archive_sessions", "archive_turns"),
            manager("usage_totals", "usage_daily", "usage_requests", "usage_runs"),
            manager("guest_mode_schedule"),
            manager("request_rules"),
        )

        async def snapshot(*_args):
            self.snapshots.append((self.gate._writer_active, self.lock.locked()))
            current = deepcopy(self.state)
            current.config = deepcopy(self.subentry.data)
            current.title = self.subentry.title
            await self.pause("snapshot")
            return current

        async def persist(*_args):
            await self.pause("persist")

        monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: self.journal)
        monkeypatch.setattr(
            restore_recovery, "_durable_managers", AsyncMock(return_value=self.managers)
        )
        monkeypatch.setattr(backup, "_snapshot_for_restore", snapshot)
        monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", persist)
        monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())
        # Exercise installed public wrappers too: calling one from inside the
        # selective transaction would deadlock on the non-reentrant gate.
        monkeypatch.setattr(
            backup,
            "async_restore_backup",
            restore_recovery.async_restore_backup_recoverably,
        )
        monkeypatch.setattr(backup, "async_create_backup", backup.async_create_backup)
        from custom_components.extended_openai_conversation_responses import (
            management_ui,
        )

        monkeypatch.setattr(
            management_ui, "async_restore_backup", management_ui.async_restore_backup
        )
        monkeypatch.setattr(
            management_ui, "async_create_backup", management_ui.async_create_backup
        )
        agent_maintenance._install_backup_guards()

    async def pause(self, stage):
        if self.pause_at == stage:
            self.pause_at = None
            self.paused.set()
            await self.resume.wait()

    async def restore(self):
        return await transfer.async_restore_transfer(
            self.hass,
            self.entry,
            self.subentry,
            self.imported,
            sections=[transfer.SECTION_KNOWLEDGE],
        )

    async def observe(self):
        async with self.gate.shared():
            return deepcopy(self.state)


@pytest.fixture
def transaction(monkeypatch):
    return RestoreHarness(monkeypatch)


async def test_selective_restore_drains_reader_before_preserving_destination(
    transaction,
):
    """A conversation's last writes survive in every unselected destination section."""
    tx = transaction
    entered = asyncio.Event()
    release = asyncio.Event()
    tx.pause_at = "apply"

    async def conversation():
        async with tx.gate.shared():
            entered.set()
            await release.wait()
            tx.state.memories.append(
                replace(tx.state.memories[0], memory_id="new-memory")
            )
            tx.state.usage_totals.conversation_count += 1
            tx.subentry.data["prompt"] = "Written by the existing shared operation"

    reader = asyncio.create_task(conversation())
    await asyncio.wait_for(entered.wait(), 1)
    restore = asyncio.create_task(tx.restore())
    try:
        await asyncio.sleep(0)
        assert tx.snapshots == []
        assert tx.gate._waiting_writers == 1
        assert not tx.lock.locked()
        late_reader = asyncio.create_task(tx.observe())
        await asyncio.sleep(0)
        assert not late_reader.done()
        release.set()
        await asyncio.wait_for(tx.paused.wait(), 1)
        assert not late_reader.done()
    finally:
        release.set()
        tx.resume.set()
        await asyncio.wait_for(asyncio.gather(reader, restore), 2)

    observed = await asyncio.wait_for(late_reader, 1)
    assert observed.knowledge == []
    assert [record.memory_id for record in observed.memories] == [
        "memory-1",
        "new-memory",
    ]
    assert observed.usage_totals.conversation_count == 5
    assert tx.subentry.data["prompt"] == "Written by the existing shared operation"
    assert restore.result()["transfer"]["selected_sections"] == ["knowledge"]
    assert tx.snapshots == [(True, False), (True, True)]
    assert [record["phase"] for record in tx.journal.saved] == ["applying", "committed"]
    assert tx.journal.data is None


@pytest.mark.parametrize("stage", ["snapshot", "apply", "persist"])
async def test_cancellation_waits_for_selective_transaction_to_commit(
    transaction, stage
):
    """Even cancellation during planning holds exclusivity until the journal finishes."""
    tx = transaction
    tx.pause_at = stage
    restore = asyncio.create_task(tx.restore())
    await asyncio.wait_for(tx.paused.wait(), 1)
    observer = asyncio.create_task(tx.observe())
    try:
        for _ in range(2):
            restore.cancel()
            await asyncio.sleep(0)
            assert not restore.done()
            assert not observer.done()
    finally:
        tx.resume.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(restore, 2)
    observed = await asyncio.wait_for(observer, 1)
    assert observed.knowledge == []
    assert tx.journal.data is None
    assert tx.journal.removed == 1
    assert not tx.lock.locked()
    assert not tx.gate._writer_active


async def test_cancelled_waiter_does_not_snapshot_or_write(transaction):
    tx = transaction
    async with tx.gate.shared():
        restore = asyncio.create_task(tx.restore())
        await asyncio.sleep(0)
        assert tx.gate._waiting_writers == 1
        restore.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(restore, 1)
    assert tx.snapshots == tx.writes == tx.journal.saved == []
    assert tx.gate._waiting_writers == 0
    await asyncio.wait_for(tx.observe(), 1)


async def test_apply_failure_rolls_back_before_deferred_cancellation(transaction):
    tx = transaction
    before = deepcopy(tx.state)
    tx.pause_at = "apply"
    tx.fail_knowledge_writes = 1
    restore = asyncio.create_task(tx.restore())
    await asyncio.wait_for(tx.paused.wait(), 1)
    observer = asyncio.create_task(tx.observe())
    restore.cancel()
    await asyncio.sleep(0)
    assert not observer.done()
    tx.resume.set()
    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await asyncio.wait_for(restore, 2)
    assert await asyncio.wait_for(observer, 1) == before
    assert tx.journal.data is None
    assert not tx.lock.locked()


async def test_failed_rollback_keeps_journal_for_existing_recovery(transaction):
    tx = transaction
    before = deepcopy(tx.state)
    tx.fail_knowledge_writes = 2
    with pytest.raises(backup.BackupError, match="recovery is still pending"):
        await asyncio.wait_for(tx.restore(), 2)
    assert tx.journal.data["phase"] == "applying"
    assert not tx.gate._writer_active
    assert not tx.lock.locked()
    async with tx.gate.exclusive():
        assert await restore_recovery.async_recover_pending_restore(
            tx.hass, tx.entry, tx.subentry
        )
    assert tx.state == before
    assert tx.journal.data is None


async def test_invalid_selection_releases_gate_without_journal(transaction):
    tx = transaction
    with pytest.raises(backup.BackupError):
        await transfer.async_restore_transfer(
            tx.hass, tx.entry, tx.subentry, tx.imported, sections=["unknown"]
        )
    assert tx.snapshots == tx.writes == tx.journal.saved == []
    await asyncio.wait_for(tx.observe(), 1)


async def test_preview_does_not_acquire_restore_gate_or_write(transaction):
    tx = transaction
    async with tx.gate.shared():
        target, preview = await asyncio.wait_for(
            transfer.async_materialize_restore(
                tx.hass, tx.entry, tx.subentry, tx.imported
            ),
            1,
        )
    assert target.knowledge == []
    assert preview["selected_sections"] == ["knowledge"]
    assert tx.snapshots == [(False, False)]
    assert tx.writes == tx.journal.saved == []


async def test_full_restore_queues_behind_selective_snapshot_without_lock_inversion(
    transaction,
):
    tx = transaction
    full_target = deepcopy(tx.state)
    tx.pause_at = "snapshot"
    selective = asyncio.create_task(tx.restore())
    await asyncio.wait_for(tx.paused.wait(), 1)
    full = asyncio.create_task(
        backup.async_restore_backup(tx.hass, tx.entry, tx.subentry, full_target)
    )
    try:
        await asyncio.sleep(0)
        assert tx.gate._waiting_writers == 1
        assert not tx.lock.locked()
        assert tx.snapshots == [(True, False)]
        assert not full.done()
    finally:
        tx.resume.set()
    results = await asyncio.wait_for(asyncio.gather(selective, full), 2)
    assert all(result["status"] == "restored" for result in results)
    assert tx.state == full_target
    assert tx.snapshots == [(True, False), (True, True), (True, True)]
    assert tx.journal.removed == 2
    assert not tx.lock.locked()
    assert not tx.gate._writer_active
