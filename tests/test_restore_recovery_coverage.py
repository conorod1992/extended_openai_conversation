"""Focused residual coverage for restore recovery validation and discovery paths."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import restore_recovery
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from tests.test_backup import _document


def _states():
    target = backup.inspect_backup(_document(), "agent-new")
    rollback = deepcopy(target)
    rollback.title = "Before restore"
    rollback.config = {**rollback.config, "prompt": "old configuration"}
    target.title = "Restored"
    target.config = {**target.config, "prompt": "new configuration"}
    return target, rollback


def _journal():
    target, rollback = _states()
    return restore_recovery._new_journal("entry-1", "agent-new", target, rollback)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: ["not", "a", "mapping"],
        lambda value: {**value, "extra": True},
        lambda value: {**value, "transaction_id": ""},
        lambda value: {**value, "entry_id": "wrong-entry"},
        lambda value: {**value, "subentry_id": "wrong-agent"},
        lambda value: {**value, "phase": "unknown"},
        lambda value: {**value, "created_at": "not-a-timestamp"},
    ],
)
def test_load_journal_rejects_corrupt_envelope(mutate) -> None:
    with pytest.raises(backup.BackupError, match="Pending restore transaction is corrupted"):
        restore_recovery._load_journal(mutate(_journal()), "entry-1", "agent-new")


def test_load_journal_rejects_invalid_embedded_backup() -> None:
    journal = _journal()
    journal["target"] = {"not": "a valid backup"}

    with pytest.raises(backup.BackupError, match="Pending restore transaction is corrupted"):
        restore_recovery._load_journal(journal, "entry-1", "agent-new")


def test_load_journal_returns_validated_target_and_rollback() -> None:
    journal = _journal()

    loaded, target, rollback = restore_recovery._load_journal(
        journal, "entry-1", "agent-new"
    )

    assert loaded == journal
    assert target.title == "Restored"
    assert rollback.title == "Before restore"


def test_active_agent_prefers_exact_registered_agent(monkeypatch) -> None:
    from homeassistant.components import conversation

    exact = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-new"),
    )
    hass = SimpleNamespace(data={})
    monkeypatch.setattr(conversation, "async_get_agent", lambda *_args: exact)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-new") is exact


@pytest.mark.parametrize("exc", [KeyError("missing"), ValueError("bad")])
def test_active_agent_scans_loaded_entities_when_core_lookup_fails(monkeypatch, exc) -> None:
    from homeassistant.components import conversation

    wrong = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="other-agent"),
    )
    exact = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="agent-new"),
    )
    component = SimpleNamespace(entities=[wrong, exact])
    hass = SimpleNamespace(data={conversation.DATA_COMPONENT: component})

    def fail_lookup(*_args):
        raise exc

    monkeypatch.setattr(conversation, "async_get_agent", fail_lookup)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-new") is exact


def test_active_agent_returns_none_when_no_exact_agent_exists(monkeypatch) -> None:
    from homeassistant.components import conversation

    wrong = SimpleNamespace(
        entry=SimpleNamespace(entry_id="entry-1"),
        subentry=SimpleNamespace(subentry_id="other-agent"),
    )
    hass = SimpleNamespace(
        data={conversation.DATA_COMPONENT: SimpleNamespace(entities=[wrong])}
    )
    monkeypatch.setattr(conversation, "async_get_agent", lambda *_args: wrong)

    assert restore_recovery._active_agent(hass, "entry-1", "agent-new") is None


def test_runtime_reset_clears_distinct_registry_and_agent_runtime_objects(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import (
        continuity,
        function_groups,
        request_rules,
    )

    def continuity_state(label):
        return SimpleNamespace(
            label=label,
            _sessions={"x": 1},
            _memory_bundles={"x": 1},
            _pending_ends={"x"},
            _ignored_conversation_ids={"x": 1},
        )

    def rule_state(label):
        return SimpleNamespace(label=label, _conversation_overrides={"x": 1})

    def group_state(label):
        return SimpleNamespace(label=label, _sessions={"x": 1}, _last_request={"x": 1})

    key = ("entry-1", "agent-new")
    registry_continuity = continuity_state("registry")
    agent_continuity = continuity_state("agent")
    registry_rules = rule_state("registry")
    agent_rules = rule_state("agent")
    registry_groups = group_state("registry")
    agent_groups = group_state("agent")
    agent = SimpleNamespace(
        _continuity=agent_continuity,
        _request_rule_runtime=agent_rules,
        _function_groups_runtime=agent_groups,
        _usage=None,
    )
    hass = SimpleNamespace(
        data={
            continuity._MANAGERS: {key: registry_continuity},
            request_rules._RUNTIMES: {key: registry_rules},
            function_groups._RUNTIMES: {key: registry_groups},
        }
    )
    monkeypatch.setattr(restore_recovery, "_active_agent", lambda *_args: agent)

    restore_recovery.reset_restored_runtime(hass, *key)

    for current in (registry_continuity, agent_continuity):
        assert current._sessions == {}
        assert current._memory_bundles == {}
        assert current._pending_ends == set()
        assert current._ignored_conversation_ids == {}
    for current in (registry_rules, agent_rules):
        assert current._conversation_overrides == {}
    for current in (registry_groups, agent_groups):
        assert current._sessions == {}
        assert current._last_request == {}
    assert hass.data[continuity._MANAGERS][key] is agent_continuity
    assert hass.data[request_rules._RUNTIMES][key] is agent_rules
    assert hass.data[function_groups._RUNTIMES][key] is agent_groups


def test_persisted_subentry_matcher_rejects_malformed_snapshots() -> None:
    prepared, _rollback = _states()
    valid = {
        "entries": [
            {
                "entry_id": "entry-1",
                "subentries": [
                    {
                        "subentry_id": "agent-new",
                        "title": prepared.title,
                        "data": deepcopy(prepared.config),
                    }
                ],
            }
        ]
    }

    assert restore_recovery._persisted_subentry_matches(
        valid, "entry-1", "agent-new", prepared
    ) is True
    assert restore_recovery._persisted_subentry_matches(
        None, "entry-1", "agent-new", prepared
    ) is False
    assert restore_recovery._persisted_subentry_matches(
        {"entries": {}}, "entry-1", "agent-new", prepared
    ) is False
    assert restore_recovery._persisted_subentry_matches(
        {"entries": [{"entry_id": "entry-1", "subentries": {}}]},
        "entry-1",
        "agent-new",
        prepared,
    ) is False
    assert restore_recovery._persisted_subentry_matches(
        {"entries": [{"entry_id": "other", "subentries": []}]},
        "entry-1",
        "agent-new",
        prepared,
    ) is False

    wrong_title = deepcopy(valid)
    wrong_title["entries"][0]["subentries"][0]["title"] = "Wrong"
    assert restore_recovery._persisted_subentry_matches(
        wrong_title, "entry-1", "agent-new", prepared
    ) is False


def test_persisted_subentry_matcher_stops_after_matching_entry() -> None:
    prepared, _rollback = _states()
    value = {
        "entries": [
            {"entry_id": "entry-1", "subentries": []},
            {
                "entry_id": "entry-1",
                "subentries": [
                    {
                        "subentry_id": "agent-new",
                        "title": prepared.title,
                        "data": prepared.config,
                    }
                ],
            },
        ]
    }

    assert restore_recovery._persisted_subentry_matches(
        value, "entry-1", "agent-new", prepared
    ) is False


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
    conversation_one = SimpleNamespace(subentry_id="agent-1", subentry_type="conversation")
    ignored = SimpleNamespace(subentry_id="sensor-1", subentry_type="other")
    conversation_two = SimpleNamespace(subentry_id="agent-2", subentry_type="conversation")
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


@pytest.mark.asyncio
async def test_install_restore_recovery_wraps_once_and_delegates(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    original_backup_restore = backup.async_restore_backup
    original_management_restore = management_ui.async_restore_backup
    original_installed = restore_recovery._INSTALLED
    delegated = AsyncMock(return_value={"status": "restored"})

    async def base_restore(*_args, **_kwargs):
        return {"status": "base"}

    try:
        restore_recovery._INSTALLED = False
        monkeypatch.setattr(backup, "async_restore_backup", base_restore)
        monkeypatch.setattr(management_ui, "async_restore_backup", base_restore)
        monkeypatch.setattr(
            restore_recovery, "async_restore_backup_recoverably", delegated
        )

        restore_recovery.install_restore_recovery()
        first = backup.async_restore_backup
        assert getattr(first, "_extended_openai_restart_recovery", False) is True
        assert management_ui.async_restore_backup is first
        assert await first("hass", "entry", "subentry", {"backup": True}) == {
            "status": "restored"
        }
        delegated.assert_awaited_once_with(
            "hass", "entry", "subentry", {"backup": True}
        )

        restore_recovery.install_restore_recovery()
        assert backup.async_restore_backup is first
        assert management_ui.async_restore_backup is first
    finally:
        backup.async_restore_backup = original_backup_restore
        management_ui.async_restore_backup = original_management_restore
        restore_recovery._INSTALLED = original_installed


def test_install_restore_recovery_does_not_double_wrap_marked_restore(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    original_backup_restore = backup.async_restore_backup
    original_management_restore = management_ui.async_restore_backup
    original_installed = restore_recovery._INSTALLED

    async def already_wrapped(*_args, **_kwargs):
        return {"status": "existing"}

    already_wrapped._extended_openai_restart_recovery = True

    try:
        restore_recovery._INSTALLED = False
        monkeypatch.setattr(backup, "async_restore_backup", already_wrapped)
        sentinel = AsyncMock()
        monkeypatch.setattr(management_ui, "async_restore_backup", sentinel)

        restore_recovery.install_restore_recovery()

        assert backup.async_restore_backup is already_wrapped
        assert management_ui.async_restore_backup is sentinel
        assert restore_recovery._INSTALLED is True
    finally:
        backup.async_restore_backup = original_backup_restore
        management_ui.async_restore_backup = original_management_restore
        restore_recovery._INSTALLED = original_installed
