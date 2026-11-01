"""Regression tests for restart-safe full restore transactions."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    management_function_repair,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.restore_participants import participants
from tests.test_backup import _document
from tests.test_subsystem_lifecycle_apis import _seeded_continuity

# Template configuration validation needs HA's event-loop context.
pytestmark = pytest.mark.usefixtures("hass")


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
    async_create_task = staticmethod(asyncio.create_task)

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
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    recovered = await restore_recovery.async_recover_pending_restore(
        hass, entry, subentry
    )
    return hass, entry, subentry, store, selected, recovered


async def test_uncommitted_restart_always_rolls_back(monkeypatch) -> None:
    """Every interruption before the durable commit decision restores old state."""
    target, rollback = _states()
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)

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


async def test_committed_restart_finishes_target_then_cleans_journal(
    monkeypatch,
) -> None:
    """Once commit intent is durable, recovery always converges on the target."""
    target, rollback = _states()
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
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
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)

    with pytest.raises(
        backup.BackupError, match="journal could not be durably verified"
    ):
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
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(
        restore_recovery, "_async_write_journal_verified", verified_write
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", MagicMock())

    with pytest.raises(backup.BackupError, match="previous agent state was recovered"):
        await restore_recovery.async_restore_backup_recoverably(
            hass, entry, subentry, _document()
        )

    assert selected == ["Jarvis", "Before restore"]
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
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(
        restore_recovery, "_async_write_journal_verified", verified_write
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)

    with pytest.raises(
        backup.BackupError, match="commit decision could not be verified"
    ):
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
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", flaky_apply)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
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


async def test_committed_state_survives_cleanup_failure_and_retries(
    monkeypatch,
) -> None:
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

    def cleanup(*_args, **_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 1:
            raise RuntimeError("cleanup interrupted")

    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_args: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=participants())
    )
    monkeypatch.setattr(restore_recovery, "_apply_prepared", apply)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
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


def test_runtime_reset_keeps_authoritative_objects_and_rebinds_usage(
    monkeypatch,
) -> None:
    """Stale transient state is cleared without splitting live runtime identity."""
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
        sensor as sensor_module,
        usage,
    )

    key = ("entry-1", "agent-new")
    continuity_manager = _seeded_continuity(
        _sessions={"old": object()},
        _memory_bundles={"old": object()},
        _pending_ends={"old"},
        _ignored_conversation_ids={"old": None},
    )
    rule_runtime = request_rules.RequestRuleRuntime()
    rule_runtime.set("old", {"model": "old"})
    group_runtime = function_groups.FunctionGroupRuntime()
    group_runtime.begin("old", 5)
    group_runtime._last_request = {"x": 1}
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
            usage._VOLATILE_USAGE_MANAGERS: {key: fallback_usage},
        }
    )
    managers = participants(
        memory=object(),
        temporary=object(),
        knowledge=object(),
        archive=object(),
        usage=durable_usage,
        guest_mode=object(),
        request_rules=object(),
    )
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: agent)
    rebind_sensors = MagicMock(return_value=4)
    monkeypatch.setattr(sensor_module, "rebind_usage_sensors", rebind_sensors)

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
    assert key not in hass.data[usage._VOLATILE_USAGE_MANAGERS]
    assert agent._usage is durable_usage
    rebind_sensors.assert_called_once_with(
        hass, "agent-new", fallback_usage, durable_usage
    )


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


async def test_configuration_is_forced_to_disk_before_journal_cleanup(
    monkeypatch,
) -> None:
    """The target subentry must be present in a fresh Core-store disk read."""
    hass = FakeHass()
    entry, subentry = _identity()
    before = management_function_repair.persisted_config_projection(subentry)
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
    after = management_function_repair.persisted_config_projection(subentry)
    assert after.revision != before.revision
    assert (
        management_function_repair.normalized_persisted_config_snapshot(after)[0][
            "prompt"
        ]
        == "new configuration"
    )


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


@pytest.mark.asyncio
async def test_persist_config_entries_requires_core_store_internals() -> None:
    prepared, _rollback = _states()
    hass = SimpleNamespace(config_entries=SimpleNamespace())

    with pytest.raises(backup.BackupError, match="durably verified"):
        await restore_recovery._async_persist_config_entries(
            hass, "entry-1", "agent-new", prepared
        )


@pytest.mark.asyncio
async def test_persist_config_entries_wraps_store_failures(monkeypatch) -> None:
    prepared, _rollback = _states()

    async def fail_save(_value):
        raise OSError("disk unavailable")

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            _store=SimpleNamespace(
                version=1,
                key="core.config_entries",
                minor_version=1,
                async_save=fail_save,
            ),
            _data_to_save=lambda: {"entries": []},
        )
    )

    class Verifier:
        @classmethod
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(restore_recovery, "Store", Verifier)

    with pytest.raises(backup.BackupError, match="durably verified"):
        await restore_recovery._async_persist_config_entries(
            hass, "entry-1", "agent-new", prepared
        )


@pytest.mark.asyncio
async def test_recover_all_pending_restores_only_visits_conversation_subentries(
    monkeypatch,
) -> None:
    conversation_one = SimpleNamespace(
        subentry_id="agent-1", subentry_type="conversation"
    )
    ignored = SimpleNamespace(subentry_id="sensor-1", subentry_type="other")
    conversation_two = SimpleNamespace(
        subentry_id="agent-2", subentry_type="conversation"
    )
    entry_one = SimpleNamespace(
        entry_id="entry-1",
        subentries={"agent-1": conversation_one, "sensor-1": ignored},
    )
    entry_two = SimpleNamespace(
        entry_id="entry-2", subentries={"agent-2": conversation_two}
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=MagicMock(return_value=[entry_one, entry_two])
        )
    )
    recover = AsyncMock(return_value=True)
    monkeypatch.setattr(restore_recovery, "async_recover_pending_restore", recover)

    await restore_recovery.async_recover_pending_restores(hass)

    hass.config_entries.async_entries.assert_called_once_with(DOMAIN)
    assert recover.await_count == 2
    recover.assert_any_await(hass, entry_one, conversation_one)
    recover.assert_any_await(hass, entry_two, conversation_two)


class _JournalStore:
    """Minimal Store stand-in for journal verification tests."""

    def __init__(self, *, persisted=None, save_error=None, load_error=None) -> None:
        self.persisted = persisted
        self.save_error = save_error
        self.load_error = load_error
        self.saved = None

    async def async_save(self, value) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved = value

    async def async_load(self):
        if self.load_error is not None:
            raise self.load_error
        return self.persisted


@pytest.mark.parametrize("failure_at", ["save", "load"])
async def test_journal_verification_unavailable_preserves_underlying_failure(
    failure_at,
) -> None:
    """An unreadable durability decision is distinct from a verified mismatch."""
    error = OSError(f"{failure_at} unavailable")
    store = _JournalStore(
        persisted={"phase": "applying"},
        save_error=error if failure_at == "save" else None,
        load_error=error if failure_at == "load" else None,
    )

    with pytest.raises(restore_recovery._JournalVerificationUnavailable) as exc_info:
        await restore_recovery._async_write_journal_verified(
            store, {"phase": "applying"}
        )

    assert exc_info.value.__cause__ is error


async def test_journal_verification_requires_exact_read_back() -> None:
    """A swallowed or stale Store write must not count as durable journal state."""
    journal = {"phase": "committed", "transaction_id": "tx-1"}
    store = _JournalStore(persisted={**journal, "phase": "applying"})

    assert await restore_recovery._async_write_journal_verified(store, journal) is False
    assert store.saved == journal


async def test_durable_managers_bypasses_volatile_usage_fallback(monkeypatch) -> None:
    """Restore must resolve the original durable Usage manager, never fallback RAM."""
    from custom_components.extended_openai_conversation_responses import (
        conversation_archive,
        guest_mode,
        knowledge,
        memory,
        request_rules,
        temporary_memory,
        usage,
    )

    expected = {}
    getters = {
        "memory": (memory, "async_get_memory"),
        "temporary": (temporary_memory, "async_get_temporary_memory"),
        "knowledge": (knowledge, "async_get_knowledge"),
        "archive": (conversation_archive, "async_get_archive"),
        "usage": (usage, "async_get_durable_usage"),
        "guest_mode": (guest_mode, "async_get_guest_mode"),
        "request_rules": (request_rules, "async_get_request_rules"),
    }

    mocks = []
    for field, (module, name) in getters.items():
        value = object()
        expected[field] = value
        mock = AsyncMock(return_value=value)
        monkeypatch.setattr(module, name, mock)
        mocks.append(mock)

    hass = object()
    result = await restore_recovery._durable_managers(hass, "entry-1", "agent-1")

    assert result == participants(**expected)
    for mock in mocks:
        mock.assert_awaited_once_with(hass, "entry-1", "agent-1")


@pytest.mark.parametrize("invalid", [None, {"conversation:browser": "missing-session"}, {"": "session"}])
async def test_local_archive_bindings_validate_without_entering_portable_backup(invalid):
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        ConversationArchive,
    )
    from custom_components.extended_openai_conversation_responses.scope import (
        user_scope,
    )
    from tests.test_conversation_archive import FakeArchiveStorage

    archive = ConversationArchive(FakeArchiveStorage(), "agent-new")
    await archive.async_initialize()
    session = await archive.async_begin_session("conversation:browser", user_scope("alice", source="authenticated_user"), "browser",
        archive_enabled=True, shared_archive_enabled=False, inactivity_minutes=30)
    await archive.async_make_private(session.session_id)
    target, rollback = _states()
    target.archive_sessions = list(archive._sessions.values())
    target.archive_active = {"conversation:browser": session.session_id}
    assert "active" not in await archive.async_backup_data()
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
    _, recovered, _ = await restore_recovery._async_load_journal(journal, "entry-1", "agent-new")
    assert recovered.archive_active == target.archive_active
    with pytest.raises(backup.BackupError, match="archive"):
        backup.inspect_backup(journal["target"], "agent-new")
    journal["rollback"]["archive"]["active"] = invalid
    with pytest.raises(backup.BackupError, match="corrupted"):
        await restore_recovery._async_load_journal(journal, "entry-1", "agent-new")


async def test_unretained_archive_sessions_are_local_content_free_recovery_only():
    from dataclasses import asdict, replace

    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        ArchiveTurn,
        ConversationArchive,
    )
    from custom_components.extended_openai_conversation_responses.scope import (
        unretained_scope,
    )
    from tests.test_conversation_archive import FakeArchiveStorage

    storage = FakeArchiveStorage()
    archive = ConversationArchive(storage, "agent-new")
    await archive.async_initialize()
    scope = unretained_scope()
    session = await archive.async_begin_session(
        "conversation:browser",
        scope,
        "browser",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session.retention_state == "unretained"
    target, rollback = _states()
    target.archive_sessions = [session]
    target.archive_turns = []
    target.archive_active = {"conversation:browser": session.session_id}
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
    _, recovered, _ = await restore_recovery._async_load_journal(journal, "entry-1", "agent-new")
    await archive.async_replace_backup(
        recovered.archive_sessions,
        recovered.archive_turns,
        active=recovered.archive_active,
    )
    continued = await archive.async_begin_session(
        "conversation:browser",
        scope,
        "browser",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert continued.session_id == session.session_id
    assert continued.retention_state == "unretained"
    assert (
        await archive.async_record_turn(
            continued.session_id,
            run_id="ignored",
            user_text="UNRETAINED TEXT",
            assistant_text="UNRETAINED RESPONSE",
            successful=True,
        )
        is None
    )
    assert await archive.async_backup_data() == {"sessions": [], "turns": []}
    assert session.session_id not in str(storage.metadata)
    portable = deepcopy(journal["target"])
    portable["archive"].pop("active")
    with pytest.raises(backup.BackupError, match="archive"):
        backup.inspect_backup(portable, "agent-new")
    with pytest.raises(ValueError, match="archive session metadata"):
        await archive.async_replace_backup([session], [])
    turn = ArchiveTurn(
        "injected",
        session.session_id,
        None,
        session.started_at,
        "must not retain",
        "must not retain",
        True,
    )
    with pytest.raises(
        ValueError, match="unretained archive sessions cannot contain turns"
    ):
        archive.validate_backup_data(
            {
                "sessions": [asdict(replace(session, turn_count=1))],
                "turns": [asdict(turn)],
            },
            "agent-new",
            local_recovery=True,
        )

@pytest.mark.parametrize(
    "outcome", ["no_restore", "success", "rollback", "pending_rollback"]
)
async def test_failed_knowledge_restore_preserves_retained_and_private_continuity(
    monkeypatch,
    outcome,
):
    from dataclasses import replace
    from custom_components.extended_openai_conversation_responses import continuity
    from custom_components.extended_openai_conversation_responses.conversation_archive import (
        ConversationArchive,
    )
    from custom_components.extended_openai_conversation_responses.scope import (
        user_scope,
    )
    from custom_components.extended_openai_conversation_responses.knowledge import (
        KnowledgeLibrary,
    )
    from tests.test_conversation_archive import FakeArchiveStorage
    from tests.test_knowledge import FakeStorage

    hass = FakeHass()
    entry, subentry = _identity()
    target, baseline = _states()
    archive = ConversationArchive(FakeArchiveStorage(), subentry.subentry_id)
    await archive.async_initialize()
    manager = continuity.ConversationContinuity(subentry.subentry_id)
    sessions = []
    for name in ("retained", "private"):
        scope = user_scope(name, source="test")
        resolved = await manager.async_resolve("user", scope, None, None, 30)
        await manager.async_record_success(
            resolved.key, resolved.claim_token, [], extra_system_prompt="before restore"
        )
        session = await archive.async_begin_session(
            f"continuity:{resolved.key}",
            scope,
            resolved.conversation_id,
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )
        if name == "private":
            await archive.async_make_private(session.session_id)
        sessions.append((scope, resolved, session))
    unbound = await manager.async_resolve(
        "user", user_scope("unbound", source="test"), None, None, 30
    )
    await manager.async_record_success(unbound.key, unbound.claim_token, [])
    baseline.archive_sessions = list(archive._sessions.values())
    baseline.archive_turns = []
    baseline.archive_active = dict(archive._active)
    target.archive_sessions = deepcopy(baseline.archive_sessions)
    target.archive_turns = []
    target.archive_active = dict(baseline.archive_active)
    target.preserve_retained_continuity = True
    target.knowledge[0] = replace(
        target.knowledge[0], content="Changed restored knowledge"
    )
    storage = FakeStorage()
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    await library.async_replace_backup(baseline.knowledge)
    before = deepcopy(storage.data)
    replace_knowledge = library.async_replace_backup
    calls = 0

    async def fail_after_write(records):
        nonlocal calls
        calls += 1
        await replace_knowledge(records)
        if (outcome in {"rollback", "pending_rollback"} and calls == 1) or (
            outcome == "pending_rollback" and calls == 2
        ):
            raise OSError("Knowledge acknowledgement failed after durable write")

    monkeypatch.setattr(library, "async_replace_backup", fail_after_write)
    dummy = lambda: SimpleNamespace(async_replace_backup=AsyncMock())
    managers = participants(
        memory=dummy(),
        temporary=dummy(),
        knowledge=library,
        archive=archive,
        usage=dummy(),
        guest_mode=dummy(),
        request_rules=dummy(),
    )
    store = MemoryJournalStore()
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: store)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=managers)
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=baseline)
    )
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    hass.data[continuity._MANAGERS] = {(entry.entry_id, subentry.subentry_id): manager}
    if outcome == "rollback":
        with pytest.raises(
            backup.BackupError, match="previous agent state was recovered"
        ):
            await backup.async_restore_backup(hass, entry, subentry, target)
    elif outcome == "pending_rollback":
        with pytest.raises(backup.BackupError, match="recovery is still pending"):
            await backup.async_restore_backup(hass, entry, subentry, target)
        assert store.data is not None
        assert await restore_recovery.async_recover_pending_restore(
            hass, entry, subentry
        )
    elif outcome == "success":
        assert (await backup.async_restore_backup(hass, entry, subentry, target))[
            "status"
        ] == "restored"
    assert (
        calls
        == {"no_restore": 0, "success": 1, "rollback": 2, "pending_rollback": 3}[
            outcome
        ]
    )
    assert (storage.data == before) is (outcome != "success")
    assert store.data is None
    assert (unbound.key in manager._sessions) is (outcome == "no_restore")
    for scope, original, session in sessions:
        continued = await manager.async_resolve("user", scope, None, None, 30)
        assert continued.conversation_id == original.conversation_id
        active = await archive.async_begin_session(
            f"continuity:{continued.key}",
            scope,
            continued.conversation_id,
            archive_enabled=True,
            shared_archive_enabled=False,
            inactivity_minutes=30,
        )
        assert active.session_id == session.session_id
    assert len(archive._sessions) == 2
