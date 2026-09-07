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
        self.drop_next_save = False

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        if self.fail_next_save:
            self.fail_next_save = False
            raise OSError("journal unavailable")
        if self.drop_next_save:
            self.drop_next_save = False
            return
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

    async def apply(_managers, prepared):
        selected.append(prepared.title)

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", AsyncMock()
    )
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    recovered = await restore_recovery.async_recover_pending_restore(
        hass, entry, subentry
    )
    return hass, entry, subentry, store, selected, recovered


async def test_uncommitted_restart_always_rolls_back(monkeypatch) -> None:
    """Every interruption before the durable commit decision restores old state."""
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        "entry-1", "agent-new", target, rollback
    )

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

    _hass, _entry, subentry, store, selected, recovered = await _recover_with_journal(
        monkeypatch, journal
    )

    assert recovered is True
    assert selected == ["Restored"]
    assert subentry.title == "Restored"
    assert store.data is None


@pytest.mark.parametrize("failure_mode", ["raise", "drop"])
async def test_journal_is_verified_before_first_destructive_write(
    monkeypatch, failure_mode
) -> None:
    """An unverified write-ahead record leaves every durable category untouched."""
    hass = FakeHass()
    entry, subentry = _identity()
    _target, rollback = _states()
    store = MemoryJournalStore()
    if failure_mode == "raise":
        store.fail_next_save = True
    else:
        store.drop_next_save = True
    apply = AsyncMock()

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)

    with pytest.raises(backup.BackupError, match="journal could not be durably verified"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    apply.assert_not_awaited()
    assert hass.config_entries.updates == []


async def test_existing_pending_journal_blocks_a_new_restore(monkeypatch) -> None:
    """A retry cannot overwrite the only recovery copy of an older transaction."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, rollback = _states()
    store = MemoryJournalStore(
        restore_recovery._new_journal(
            entry.entry_id, subentry.subentry_id, target, rollback
        )
    )
    managers = AsyncMock()

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(restore_recovery, "_durable_managers", managers)

    with pytest.raises(backup.BackupError, match="previous full restore"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    managers.assert_not_awaited()


async def test_unpersisted_commit_decision_rolls_back(monkeypatch) -> None:
    """A readable non-committed journal means the old state remains authoritative."""
    hass = FakeHass()
    entry, subentry = _identity()
    _target, rollback = _states()
    store = MemoryJournalStore()
    writes = 0
    selected: list[str] = []

    async def verified_write(_store, journal):
        nonlocal writes
        writes += 1
        _store.data = deepcopy(journal)
        return writes == 1

    async def apply(_managers, prepared):
        selected.append(prepared.title)

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(restore_recovery, "_async_write_journal_verified", verified_write)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", AsyncMock()
    )
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert selected == ["Agent", "Before restore"]
    assert store.data is None


async def test_unverifiable_commit_decision_stays_pending(monkeypatch) -> None:
    """An indeterminate commit write is never guessed into a rollback decision."""
    hass = FakeHass()
    entry, subentry = _identity()
    _target, rollback = _states()
    store = MemoryJournalStore()
    writes = 0
    apply = AsyncMock()

    async def verified_write(_store, journal):
        nonlocal writes
        writes += 1
        _store.data = deepcopy(journal)
        if writes == 2:
            raise restore_recovery._JournalVerificationUnavailable
        return True

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(restore_recovery, "_async_write_journal_verified", verified_write)
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)

    with pytest.raises(backup.BackupError, match="commit decision could not be verified"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert apply.await_count == 1
    assert store.data is not None
    assert store.removed == 0


async def test_interrupted_rollback_remains_retryable(monkeypatch) -> None:
    """A restart during rollback leaves the journal for an idempotent later pass."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, rollback = _states()
    journal = restore_recovery._new_journal(
        entry.entry_id, subentry.subentry_id, target, rollback
    )
    store = MemoryJournalStore(journal)
    calls = 0

    async def flaky_apply(_managers, prepared):
        nonlocal calls
        calls += 1
        assert prepared.title == "Before restore"
        if calls == 1:
            raise OSError("category unavailable")

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),) * 7)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", flaky_apply)
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", AsyncMock()
    )
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    with pytest.raises(backup.BackupError, match="could not be recovered safely"):
        await restore_recovery.async_recover_pending_restore(hass, entry, subentry)
    assert store.data is not None
    assert store.data["phase"] == restore_recovery._PHASE_APPLYING

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

    async def apply(_managers, prepared):
        selected.append(prepared.title)

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
    monkeypatch.setattr(
        restore_recovery, "_async_persist_config_entries", AsyncMock()
    )
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


def test_runtime_reset_keeps_authoritative_objects_and_rebinds_usage(monkeypatch) -> None:
    """Stale transient state is cleared without splitting live runtime identity."""
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
    fallback_usage = object()
    durable_usage = object()
    agent = SimpleNamespace(
        _continuity=continuity_manager,
        _request_rule_runtime=rule_runtime,
        _function_groups_runtime=group_runtime,
        _usage=fallback_usage,
    )
    hass = SimpleNamespace(
        data={
            continuity._MANAGERS: {key: continuity_manager},
            request_rules._RUNTIMES: {key: rule_runtime},
            function_groups._RUNTIMES: {key: group_runtime},
            runtime_failure_hardening._VOLATILE_USAGE_MANAGERS: {key: fallback_usage},
        }
    )
    managers = (object(), object(), object(), object(), durable_usage, object(), object())
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: agent)

    restore_recovery.reset_restored_runtime(hass, *key, managers)

    assert hass.data[continuity._MANAGERS][key] is continuity_manager
    assert continuity_manager._sessions == {}
    assert continuity_manager._memory_bundles == {}
    assert continuity_manager._pending_ends == set()
    assert continuity_manager._ignored_conversation_ids == {}
    assert hass.data[request_rules._RUNTIMES][key] is rule_runtime
    assert rule_runtime._conversation_overrides == {}
    assert hass.data[function_groups._RUNTIMES][key] is group_runtime
    assert group_runtime._sessions == {}
    assert group_runtime._last_request == {}
    assert key not in hass.data[runtime_failure_hardening._VOLATILE_USAGE_MANAGERS]
    assert agent._usage is durable_usage


class _VerifierStore:
    """Fresh Core-store reader used by the persistence verification tests."""

    payload = None

    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def async_load(self):
        return deepcopy(self.payload)


async def test_configuration_is_forced_to_disk_before_journal_cleanup(monkeypatch) -> None:
    """The target subentry must be present in a fresh Core-store disk read."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, _rollback = _states()
    saved = []

    def data_to_save():
        return {
            "entries": [
                {
                    "entry_id": entry.entry_id,
                    "subentries": [
                        {
                            "subentry_id": subentry.subentry_id,
                            "title": subentry.title,
                            "data": deepcopy(subentry.data),
                        }
                    ],
                }
            ]
        }

    async def save(value):
        saved.append(deepcopy(value))
        _VerifierStore.payload = deepcopy(value)

    hass.config_entries._store = SimpleNamespace(
        version=1,
        minor_version=1,
        key="core.config_entries",
        async_save=save,
    )
    hass.config_entries._data_to_save = data_to_save
    monkeypatch.setattr(restore_recovery, "Store", _VerifierStore)

    await restore_recovery._update_configuration(hass, entry, subentry, target)

    assert saved
    assert subentry.title == "Restored"
    assert subentry.data == target.config


async def test_configuration_disk_mismatch_keeps_recovery_pending(monkeypatch) -> None:
    """A swallowed Core-store write failure cannot permit journal deletion."""
    hass = FakeHass()
    entry, subentry = _identity()
    target, _rollback = _states()

    def data_to_save():
        return {
            "entries": [
                {
                    "entry_id": entry.entry_id,
                    "subentries": [
                        {
                            "subentry_id": subentry.subentry_id,
                            "title": subentry.title,
                            "data": deepcopy(subentry.data),
                        }
                    ],
                }
            ]
        }

    async def swallowed_save(_value):
        return None

    _VerifierStore.payload = {
        "entries": [
            {
                "entry_id": entry.entry_id,
                "subentries": [
                    {
                        "subentry_id": subentry.subentry_id,
                        "title": "Before restore",
                        "data": {"prompt": "old"},
                    }
                ],
            }
        ]
    }
    hass.config_entries._store = SimpleNamespace(
        version=1,
        minor_version=1,
        key="core.config_entries",
        async_save=swallowed_save,
    )
    hass.config_entries._data_to_save = data_to_save
    monkeypatch.setattr(restore_recovery, "Store", _VerifierStore)

    with pytest.raises(backup.BackupError, match="durably verified"):
        await restore_recovery._update_configuration(hass, entry, subentry, target)
