"""Fault-injection coverage for live restore transaction phase boundaries."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import restore_recovery
from tests.test_backup import _document
from tests.test_restore_recovery import (
    FakeHass,
    MemoryJournalStore,
    _identity,
    _states,
)


async def _prepare_live_restore(monkeypatch):
    """Install the common durable-store fakes for a live restore attempt."""
    hass = FakeHass()
    entry, subentry = _identity()
    _target, rollback = _states()
    store = MemoryJournalStore()
    managers = (object(),) * 7

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=managers)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", AsyncMock()
    )
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())
    return hass, entry, subentry, store, managers, rollback


async def test_apply_failure_rolls_back_before_returning_error(monkeypatch) -> None:
    """A live failure during apply restores the pre-restore state atomically."""
    hass, entry, subentry, store, _managers, rollback = await _prepare_live_restore(
        monkeypatch
    )
    applied: list[str] = []

    async def fail_target_then_restore(_managers, prepared):
        applied.append(prepared.title)
        if len(applied) == 1:
            raise OSError("target category write failed")

    monkeypatch.setattr(
        restore_recovery, "_apply_prepared", fail_target_then_restore
    )

    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert applied == ["Jarvis", "Before restore"]
    assert subentry.title == rollback.title
    assert subentry.data == rollback.config
    assert store.data is None
    assert store.removed == 1


async def test_apply_and_immediate_rollback_failure_leave_retryable_journal(
    monkeypatch,
) -> None:
    """If live rollback also fails, the applying journal survives for restart recovery."""
    hass, entry, subentry, store, _managers, _rollback = await _prepare_live_restore(
        monkeypatch
    )
    applied: list[str] = []

    async def fail_live_apply_and_rollback(_managers, prepared):
        applied.append(prepared.title)
        if len(applied) <= 2:
            raise OSError(f"phase write failed: {prepared.title}")

    monkeypatch.setattr(
        restore_recovery, "_apply_prepared", fail_live_apply_and_rollback
    )

    with pytest.raises(backup.BackupError, match="recovery is still pending"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert applied == ["Jarvis", "Before restore"]
    assert store.data is not None
    assert store.data["phase"] == restore_recovery._PHASE_APPLYING
    assert store.removed == 0

    assert (
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        is True
    )
    assert applied == ["Jarvis", "Before restore", "Before restore"]
    assert subentry.title == "Before restore"
    assert store.data is None
    assert store.removed == 1


async def test_post_commit_completion_failure_recovers_forward_only(monkeypatch) -> None:
    """After durable commit, a live completion fault must never roll back target data."""
    hass, entry, subentry, store, _managers, _rollback = await _prepare_live_restore(
        monkeypatch
    )
    applied: list[str] = []

    async def record_apply(_managers, prepared):
        applied.append(prepared.title)

    update_calls = 0

    async def fail_first_configuration_update(
        _hass, actual_entry, actual_subentry, prepared
    ):
        nonlocal update_calls
        update_calls += 1
        if update_calls == 1:
            raise OSError("config persistence interrupted")
        actual_subentry.data = deepcopy(prepared.config)
        actual_subentry.title = prepared.title

    monkeypatch.setattr(restore_recovery, "_apply_prepared", record_apply)
    monkeypatch.setattr(
        restore_recovery, "_update_configuration", fail_first_configuration_update
    )

    with pytest.raises(backup.BackupError, match="completion is pending"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert applied == ["Jarvis"]
    assert store.data is not None
    assert store.data["phase"] == restore_recovery._PHASE_COMMITTED
    assert store.removed == 0

    assert (
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        is True
    )
    assert applied == ["Jarvis", "Jarvis"]
    assert subentry.title == "Jarvis"
    assert store.data is None
    assert store.removed == 1
