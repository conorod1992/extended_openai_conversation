"""Regression tests for restart-safe full restore transactions."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import restore_recovery
from tests.test_backup import _document


class MemoryJournalStore:
    """Small durable-journal stand-in that survives simulated recovery passes."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)
        self.saved: list[dict] = []
        self.removed = 0
        self.fail_next_save = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("journal unavailable")
        self.data = deepcopy(data)
        self.saved.append(deepcopy(data))

    async def async_remove(self) -> None:
        self.data = None
        self.removed += 1


class FakeConfigEntries:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict, str]] = []

    def async_update_subentry(self, entry, subentry, *, data, title) -> None:
        self.updates.append((subentry.subentry_id, deepcopy(data), title))
        subentry.data = deepcopy(data)
        subentry.title = title


class FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.config_entries = FakeConfigEntries()


def _states():
    target = backup.inspect_backup(_document(), "agent-new")
    rollback = deepcopy(target)
    rollback.title = "Before restore"
    rollback.config = {**rollback.config, "prompt": "old configuration"}
    target.title = "Restored"
    target.config = {**target.config, "prompt": "new configuration"}
    return target, rollback


def _identity():
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-new", title="Before restore", data={"prompt": "old"}
    )
    return entry, subentry


async def _recover_with_journal(monkeypatch, journal):
    hass = FakeHass()
    entry, subentry = _identity()
    store = MemoryJournalStore(journal)
    selected: list[str] = []

    async def apply(_managers, prepared, progress):
        selected.append(prepared.title)
        for category in restore_recovery._CATEGORIES[:-1]:
            await progress(category)

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    recovered = await restore_recovery.async_recover_pending_restore(
        hass, entry, subentry
    )
    return hass, entry, subentry, store, selected, recovered


@pytest.mark.parametrize(
    ("completed", "phase"),
    [
        ([], restore_recovery._PHASE_APPLYING),
        (["persistent_memory", "temporary_memory"], restore_recovery._PHASE_APPLYING),
        (list(restore_recovery._CATEGORIES[:-1]), restore_recovery._PHASE_APPLYING),
        (["persistent_memory"], restore_recovery._PHASE_ROLLING_BACK),
    ],
)
async def test_uncommitted_restart_always_rolls_back(monkeypatch, completed, phase) -> None:
    """Every interruption before the durable commit decision restores old state."""
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        "entry-1", "agent-new", target, rollback
    )
    journal["phase"] = phase
    journal["completed_categories"] = list(completed)

    hass, entry, subentry, store, selected, recovered = await _recover_with_journal(
        monkeypatch, journal
    )

    assert recovered is True
    assert selected == ["Before restore"]
    assert subentry.title == "Before restore"
    assert store.data is None
    assert store.removed == 1

    # A second startup/recovery pass is deliberately harmless.
    assert (
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        is False
    )


async def test_committed_restart_finishes_target_then_cleans_journal(monkeypatch) -> None:
    """Once commit intent is durable, recovery always converges on the target."""
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        "entry-1", "agent-new", target, rollback
    )
    journal["phase"] = restore_recovery._PHASE_COMMITTED
    journal["completed_categories"] = list(restore_recovery._CATEGORIES)

    _hass, _entry, subentry, store, selected, recovered = await _recover_with_journal(
        monkeypatch, journal
    )

    assert recovered is True
    assert selected == ["Restored"]
    assert subentry.title == "Restored"
    assert store.data is None


async def test_journal_is_durable_before_first_destructive_write(monkeypatch) -> None:
    """Failure to create write-ahead state leaves every durable category untouched."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, rollback = _states()
    store = MemoryJournalStore()
    store.fail_next_save = True
    apply = AsyncMock()

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)

    with pytest.raises(OSError, match="journal unavailable"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    apply.assert_not_awaited()
    assert hass.config_entries.updates == []


async def test_interrupted_rollback_remains_retryable(monkeypatch) -> None:
    """A restart during rollback leaves the journal for an idempotent later pass."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, target, rollback
    )
    journal["phase"] = restore_recovery._PHASE_ROLLING_BACK
    store = MemoryJournalStore(journal)
    calls = 0

    async def flaky_apply(_managers, prepared, progress):
        nonlocal calls
        calls += 1
        assert prepared.title == "Before restore"
        if calls == 1:
            raise OSError("category unavailable")
        for category in restore_recovery._CATEGORIES[:-1]:
            await progress(category)

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", flaky_apply)
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    with pytest.raises(backup.BackupError, match="could not be recovered safely"):
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert store.data is not None
    assert store.data["phase"] == restore_recovery._PHASE_ROLLING_BACK

    assert (
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        is True
    )
    assert calls == 2
    assert store.data is None


async def test_committed_state_survives_cleanup_failure_and_retries(monkeypatch) -> None:
    """A crash-like cleanup failure never changes a durable commit into rollback."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, target, rollback
    )
    journal["phase"] = restore_recovery._PHASE_COMMITTED
    store = MemoryJournalStore(journal)
    selected: list[str] = []

    async def apply(_managers, prepared, progress):
        selected.append(prepared.title)
        for category in restore_recovery._CATEGORIES[:-1]:
            await progress(category)

    cleanup_calls = 0

    def cleanup(*_args):
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise RuntimeError("cleanup interrupted")

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", cleanup)

    with pytest.raises(backup.BackupError, match="could not be recovered safely"):
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert store.data is not None
    assert store.data["phase"] == restore_recovery._PHASE_COMMITTED

    assert (
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
        is True
    )
    assert selected == ["Restored", "Restored"]
    assert store.data is None


def test_runtime_reset_discards_pre_restore_transient_state(monkeypatch) -> None:
    """Continuity, routing, loaded groups and volatile Usage cannot survive restore."""
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
        runtime_failure_hardening,
    )

    key = ("entry-1", "agent-new")
    continuity_manager = SimpleNamespace(
        _sessions={"old": object()},
        _memory_bundles={"old": object()},
        _pending_ends={"old"},
        _ignored_conversation_ids={"old": None},
    )
    rule_runtime = SimpleNamespace(_conversation_overrides={"old": object()})
    group_runtime = SimpleNamespace(_sessions={"old": object()}, _last_request={"x": 1})
    hass = SimpleNamespace(
        data={
            continuity._MANAGERS: {key: continuity_manager},
            request_rules._RUNTIMES: {key: rule_runtime},
            function_groups._RUNTIMES: {key: group_runtime},
            runtime_failure_hardening._VOLATILE_USAGE_MANAGERS: {key: object()},
        }
    )

    restore_recovery.reset_restored_runtime(hass, *key)

    assert key not in hass.data[continuity._MANAGERS]
    assert continuity_manager._sessions == {}
    assert continuity_manager._memory_bundles == {}
    assert key not in hass.data[request_rules._RUNTIMES]
    assert rule_runtime._conversation_overrides == {}
    assert key not in hass.data[function_groups._RUNTIMES]
    assert group_runtime._sessions == {}
    assert key not in hass.data[runtime_failure_hardening._VOLATILE_USAGE_MANAGERS]
