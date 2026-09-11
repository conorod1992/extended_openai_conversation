"""Fault-injection coverage for full-restore atomicity boundaries."""

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


_BOUNDARIES = (
    "memory",
    "temporary_memory",
    "knowledge",
    "archive",
    "usage",
    "guest_mode",
    "request_rules",
)


def _prepared_args(prepared: backup.PreparedRestore) -> tuple[tuple[object, ...], ...]:
    """Return the exact argument tuples passed to each durable manager."""
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


class _FaultInjectingManager:
    """Track target/rollback application and fail one target write at most once."""

    def __init__(
        self,
        target_args: tuple[object, ...],
        rollback_args: tuple[object, ...],
        *,
        fail_target_once: bool,
    ) -> None:
        self._target_args = target_args
        self._rollback_args = rollback_args
        self._fail_target_once = fail_target_once
        self.failed = False
        self.calls = 0
        self.state = "rollback"
        self.request_retention_days = 0
        self.run_retention_days = 0

    @staticmethod
    def _matches_identity(
        actual: tuple[object, ...], expected: tuple[object, ...]
    ) -> bool:
        return len(actual) == len(expected) and all(
            current is wanted for current, wanted in zip(actual, expected, strict=True)
        )

    async def async_replace_backup(self, *args: object) -> None:
        self.calls += 1
        actual = tuple(args)
        is_target = self._matches_identity(actual, self._target_args)
        is_rollback = self._matches_identity(actual, self._rollback_args)

        # Guest Mode can legitimately have None for both target and rollback. In
        # that case the first call at its boundary is still the target application;
        # the fail-once flag ensures the rollback reapplication can proceed.
        if self._fail_target_once and not self.failed and (is_target or is_rollback):
            self.failed = True
            raise OSError("injected restore boundary failure")

        if is_target and not is_rollback:
            self.state = "target"
        elif is_rollback and not is_target:
            self.state = "rollback"
        elif is_target and is_rollback:
            self.state = "equivalent"
        else:
            raise AssertionError("restore passed unexpected manager replacement data")


@pytest.mark.parametrize("failure_index", range(len(_BOUNDARIES)), ids=_BOUNDARIES)
async def test_restore_failure_at_each_manager_boundary_rolls_back_and_retry_succeeds(
    monkeypatch, failure_index: int
) -> None:
    """Every manager-boundary failure restores old state and remains retryable."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, rollback = _states()
    store = MemoryJournalStore()

    target_args = _prepared_args(target)
    rollback_args = _prepared_args(rollback)
    managers = tuple(
        _FaultInjectingManager(
            target_args[index],
            rollback_args[index],
            fail_target_once=index == failure_index,
        )
        for index in range(len(_BOUNDARIES))
    )
    persist_config = AsyncMock()
    reset_runtime = MagicMock()

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=managers)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", persist_config
    )
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", reset_runtime)

    with pytest.raises(
        backup.BackupError, match="Restore failed; the previous agent state was recovered"
    ):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert managers[failure_index].failed is True
    assert store.data is None
    assert store.removed == 1
    assert subentry.title == rollback.title
    assert subentry.data == rollback.config
    assert persist_config.await_count == 1
    assert reset_runtime.call_count == 1

    for index, manager in enumerate(managers):
        # Categories reached before the fault see target then rollback. Categories
        # after it are touched only by rollback. Either way, no partial target state
        # may remain externally visible after the failed transaction returns.
        assert manager.calls == (2 if index <= failure_index else 1)
        assert manager.state in {"rollback", "equivalent"}

    result = await restore_recovery.async_restore_backup_recoverably(
        hass, entry, subentry, _document()
    )

    assert result["status"] == "restored"
    assert store.data is None
    assert store.removed == 2
    assert subentry.title == target.title
    assert subentry.data == target.config
    assert persist_config.await_count == 2
    assert reset_runtime.call_count == 2

    for manager in managers:
        # All non-equivalent categories converge on target during the clean retry;
        # Guest Mode may be semantically identical (None) in both snapshots.
        assert manager.state in {"target", "equivalent"}
