"""Failure-path coverage for backup creation and restore atomicity."""

from __future__ import annotations

from copy import deepcopy
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    restore_recovery,
)
from tests.restore_participants import participant_values, participants
from tests.test_backup import FakeStorage, _document


class _StatefulReplaceManager:
    """Minimal replacement manager that exposes committed state to assertions."""

    def __init__(self, state, *, fail_on_call: int | None = None) -> None:
        self.state = deepcopy(state)
        self.fail_on_call = fail_on_call
        self.calls = 0

    async def async_replace_backup(self, *state) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("injected restore failure")
        self.state = deepcopy(state)


def _manager_state(prepared: backup.PreparedRestore) -> tuple[tuple[object, ...], ...]:
    return (
        (prepared.memories,),
        (prepared.temporary_memories,),
        (prepared.knowledge,),
        (prepared.archive_sessions, prepared.archive_turns),
        (
            prepared.usage_totals,
            prepared.usage_daily,
            prepared.usage_requests,
            prepared.usage_runs,
        ),
        (prepared.guest_mode_schedule,),
        (prepared.request_rules,),
    )


async def test_partial_restore_failure_restores_all_manager_state(
    monkeypatch, hass
) -> None:
    _journal_fixture(monkeypatch)
    target_document = _document()
    target_document["memories"]["memories"][0]["content"] = "Restored value"
    target = backup.inspect_backup(target_document, "agent-new")

    baseline_document = _document()
    baseline_document["memories"]["memories"][0]["content"] = "Original value"
    baseline = backup.inspect_backup(baseline_document, "agent-new")
    baseline_state = _manager_state(baseline)

    managers = participants(
        memory=_StatefulReplaceManager((baseline.memories,)),
        temporary=_StatefulReplaceManager(
            (baseline.temporary_memories,), fail_on_call=1
        ),
        knowledge=_StatefulReplaceManager((baseline.knowledge,)),
        archive=_StatefulReplaceManager(
            (baseline.archive_sessions, baseline.archive_turns)
        ),
        usage=_StatefulReplaceManager(
            (
                baseline.usage_totals,
                baseline.usage_daily,
                baseline.usage_requests,
                baseline.usage_runs,
            )
        ),
        guest_mode=_StatefulReplaceManager((baseline.guest_mode_schedule,)),
        request_rules=_StatefulReplaceManager((baseline.request_rules,)),
    )
    usage = managers.usage
    usage.request_retention_days = 0
    usage.run_retention_days = 0

    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=managers)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=baseline)
    )

    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-new", title="Current", data=target.config
    )

    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await backup.async_restore_backup(hass, entry, subentry, target)

    assert managers.memory.calls == 2
    assert managers.temporary.calls == 2
    assert managers.knowledge.calls == 1
    assert tuple(manager.state for manager in participant_values(managers)) == baseline_state


async def test_failed_restore_and_retry_persist_memory_across_manager_reload(
    monkeypatch, hass
) -> None:
    """Rollback and retry must survive disposing of the live Memory manager."""
    _journal_fixture(monkeypatch)
    target_document = _document()
    target_document["memories"]["memories"][0]["content"] = "Restored value"
    target = backup.inspect_backup(target_document, "agent-new")
    baseline_document = _document()
    baseline_document["memories"]["memories"][0]["content"] = "Original value"
    baseline = backup.inspect_backup(baseline_document, "agent-new")
    store = FakeStorage()
    memory = backup.PersistentMemory(store)
    await memory.async_initialize()
    await memory.async_replace_backup(baseline.memories)
    initial_storage = deepcopy(store.data)
    managers = participants(
        memory=memory,
        temporary=SimpleNamespace(async_replace_backup=AsyncMock()),
        knowledge=SimpleNamespace(
            async_replace_backup=AsyncMock(
                side_effect=[OSError("write failed"), None, None]
            )
        ),
        archive=SimpleNamespace(async_replace_backup=AsyncMock()),
        usage=SimpleNamespace(async_replace_backup=AsyncMock()),
        guest_mode=SimpleNamespace(async_replace_backup=AsyncMock()),
        request_rules=SimpleNamespace(async_replace_backup=AsyncMock()),
    )
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=managers)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=baseline)
    )
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-new", title=baseline.title, data=deepcopy(baseline.config)
    )

    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await backup.async_restore_backup(hass, entry, subentry, target)

    assert store.data == initial_storage
    reloaded = backup.PersistentMemory(store)
    await reloaded.async_initialize()
    assert backup.PersistentMemory.validate_backup_data(await reloaded.async_backup_data()) == baseline.memories

    result = await backup.async_restore_backup(hass, entry, subentry, target)

    assert result["status"] == "restored"
    reloaded = backup.PersistentMemory(store)
    await reloaded.async_initialize()
    assert backup.PersistentMemory.validate_backup_data(await reloaded.async_backup_data()) == target.memories


async def test_export_serialization_failure_removes_temp_archive_and_is_normalized(
    monkeypatch, hass, tmp_path
) -> None:
    snapshot = _document()
    snapshot["memories"]["memories"][0]["content"] = object()
    monkeypatch.setattr(
        backup, "async_collect_backup_snapshot", AsyncMock(return_value=snapshot)
    )

    created_paths: list[str] = []
    real_mkstemp = backup_transfer.tempfile.mkstemp

    def tracked_mkstemp(*args, **kwargs):
        kwargs["dir"] = tmp_path
        fd, path = real_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    monkeypatch.setattr(backup_transfer.tempfile, "mkstemp", tracked_mkstemp)
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")

    with pytest.raises(
        backup.BackupError, match="full backup archive could not be created safely"
    ):
        await backup_transfer._start_export(hass, entry, subentry)

    assert len(created_paths) == 1
    assert not os.path.exists(created_paths[0])
    assert backup_transfer._exports(hass) == {}


def _journal_fixture(monkeypatch):
    from tests.test_restore_recovery import MemoryJournalStore

    store = MemoryJournalStore()
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: store)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", lambda *_, **__: None)
