"""Focused residual coverage for backup validation and restore hardening."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    BackupError,
    PreparedRestore,
)
from custom_components.extended_openai_conversation_responses.usage import UsageTotals
from homeassistant.util import dt as dt_util


def _minimal_document() -> dict:
    """Return a structurally complete backup while validators are isolated."""
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": dt_util.utcnow().isoformat(),
        "integration_version": "test",
        "agent": {
            "title": "Jarvis",
            "source_entry_id": "entry-old",
            "source_subentry_id": "agent-old",
            "config": agent_config_defaults(),
        },
        "memories": {"memories": []},
        "temporary_memories": {"records": []},
        "knowledge": {"sources": []},
        "archive": {"sessions": [], "turns": []},
        "usage": {},
        "request_rules": {"storage_version": 1, "defaults": {}, "rules": []},
    }


def _patch_section_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backup.PersistentMemory,
        "validate_backup_data",
        staticmethod(lambda _value: []),
    )
    monkeypatch.setattr(
        backup.TemporaryMemory,
        "validate_backup_data",
        staticmethod(lambda _value: []),
    )
    monkeypatch.setattr(
        backup.KnowledgeLibrary,
        "validate_backup_data",
        staticmethod(lambda _value: []),
    )
    monkeypatch.setattr(
        backup.ConversationArchive,
        "validate_backup_data",
        staticmethod(lambda _value, _agent_id: ([], [])),
    )
    monkeypatch.setattr(
        backup.UsageManager,
        "validate_backup_data",
        staticmethod(lambda _value, _agent_id: (UsageTotals(), {}, [], [])),
    )
    monkeypatch.setattr(
        backup.RequestRules,
        "validate_backup_data",
        staticmethod(
            lambda value: {
                "storage_version": 1,
                "defaults": value.get("defaults", {}),
                "rules": value.get("rules", []),
            }
        ),
    )


def _prepared(*, config: dict | None = None) -> PreparedRestore:
    return PreparedRestore(
        title="Restored",
        config=config or agent_config_defaults(),
        memories=[],
        temporary_memories=[],
        knowledge=[],
        archive_sessions=[],
        archive_turns=[],
        usage_totals=UsageTotals(),
        usage_daily={},
        usage_requests=[],
        usage_runs=[],
        guest_mode_schedule=None,
        request_rules={"storage_version": 1, "defaults": {}, "rules": []},
        created_at=dt_util.utcnow().isoformat(),
        integration_version="test",
    )


def test_finalize_backup_rejects_oversized_legacy_export(monkeypatch) -> None:
    document = _minimal_document()
    document["agent"]["config"]["prompt"] = "x" * 2048
    monkeypatch.setattr(backup, "MAX_LEGACY_EXPORT_BYTES", 128)

    with pytest.raises(BackupError, match="too large for the legacy"):
        backup.finalize_backup_snapshot(document)


@pytest.mark.parametrize("invalid_limit", [True, 0, -1, 1.5])
def test_inspect_backup_rejects_invalid_byte_limits(invalid_limit) -> None:
    with pytest.raises(ValueError, match="byte limit is invalid"):
        backup.inspect_backup({}, "agent", max_bytes=invalid_limit)


def test_inspect_backup_rejects_limit_above_global_cap() -> None:
    with pytest.raises(ValueError, match="byte limit is invalid"):
        backup.inspect_backup({}, "agent", max_bytes=backup.MAX_BACKUP_BYTES + 1)


def test_inspect_backup_returns_prepared_restore_unchanged() -> None:
    prepared = _prepared()
    assert backup.inspect_backup(prepared, "ignored") is prepared


def test_inspect_backup_rejects_oversized_and_invalid_json_strings() -> None:
    with pytest.raises(BackupError, match="safety limit"):
        backup.inspect_backup("{}", "agent", max_bytes=1)

    with pytest.raises(BackupError, match="incomplete or corrupted"):
        backup.inspect_backup("{not-json", "agent")


def test_inspect_backup_rejects_non_serializable_and_oversized_objects() -> None:
    with pytest.raises(BackupError, match="incomplete or corrupted"):
        backup.inspect_backup({"bad": {object()}}, "agent")

    with pytest.raises(BackupError, match="safety limit"):
        backup.inspect_backup({"payload": "xx"}, "agent", max_bytes=2)


def test_inspect_backup_rejects_non_mapping_and_wrong_format() -> None:
    with pytest.raises(BackupError, match="not an Extended"):
        backup.inspect_backup([], "agent")

    with pytest.raises(BackupError, match="not an Extended"):
        backup.inspect_backup({"format": "other"}, "agent")


def test_inspect_backup_rejects_old_unknown_version() -> None:
    document = _minimal_document()
    document["version"] = 0
    with pytest.raises(BackupError, match="unsupported backup format version"):
        backup.inspect_backup(document, "agent")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_at", 123),
        ("created_at", "not-a-date"),
        ("integration_version", 123),
        ("integration_version", ""),
    ],
)
def test_inspect_backup_rejects_invalid_metadata(field, value) -> None:
    document = _minimal_document()
    document[field] = value
    with pytest.raises(BackupError, match="metadata is invalid"):
        backup.inspect_backup(document, "agent")


def test_inspect_backup_rejects_incomplete_agent_shape() -> None:
    document = _minimal_document()
    document["agent"].pop("config")
    with pytest.raises(BackupError, match="agent configuration is incomplete"):
        backup.inspect_backup(document, "agent")


def test_inspect_backup_wraps_invalid_agent_title(monkeypatch) -> None:
    document = _minimal_document()

    def _invalid_title(_title):
        raise backup.HomeAssistantError("bad title")

    monkeypatch.setattr(backup, "validate_agent_title", _invalid_title)
    with pytest.raises(BackupError, match="agent name is invalid"):
        backup.inspect_backup(document, "agent")


@pytest.mark.parametrize("identity_field", ["source_entry_id", "source_subentry_id"])
def test_inspect_backup_rejects_non_string_agent_identity(identity_field) -> None:
    document = _minimal_document()
    document["agent"][identity_field] = 1
    with pytest.raises(BackupError, match="agent identity is invalid"):
        backup.inspect_backup(document, "agent")


def test_inspect_backup_wraps_non_mapping_agent_config(monkeypatch) -> None:
    document = _minimal_document()
    document["agent"]["config"] = []
    monkeypatch.setattr(backup, "restore_redacted_secrets", lambda value: value)

    with pytest.raises(BackupError, match="agent config must be an object"):
        backup.inspect_backup(document, "agent")


def test_inspect_backup_wraps_nested_validator_errors(monkeypatch) -> None:
    document = _minimal_document()
    _patch_section_validators(monkeypatch)

    def _invalid_memories(_value):
        raise ValueError("bad memories")

    monkeypatch.setattr(
        backup.PersistentMemory,
        "validate_backup_data",
        staticmethod(_invalid_memories),
    )

    with pytest.raises(BackupError, match="bad memories"):
        backup.inspect_backup(document, "agent")


def test_inspect_backup_validates_optional_guest_mode(monkeypatch) -> None:
    document = _minimal_document()
    document["guest_mode"] = {"schedule": None}
    _patch_section_validators(monkeypatch)
    guest = object()
    monkeypatch.setattr(
        backup.GuestModeManager,
        "validate_backup_data",
        staticmethod(lambda value: guest if value == {"schedule": None} else None),
    )

    prepared = backup.inspect_backup(document, "agent-new")

    assert prepared.guest_mode_schedule is guest


@pytest.mark.asyncio
async def test_restore_rollback_failure_escalates(monkeypatch, hass) -> None:
    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(
        subentry_id="agent", title="Current", data=agent_config_defaults()
    )
    prepared = _prepared()
    rollback = _prepared()
    monkeypatch.setattr(backup, "inspect_backup", lambda _value, _agent: prepared)
    monkeypatch.setattr(backup, "_managers", AsyncMock(return_value=(object(),)))
    monkeypatch.setattr(backup, "_snapshot_for_restore", AsyncMock(return_value=rollback))
    monkeypatch.setattr(
        backup,
        "_apply_restore",
        AsyncMock(
            side_effect=[
                RuntimeError("restore failed"),
                RuntimeError("rollback failed"),
            ]
        ),
    )

    with pytest.raises(BackupError, match="could not be fully recovered"):
        await backup.async_restore_backup(hass, entry, subentry, {})


@pytest.mark.asyncio
async def test_managers_collect_every_durable_manager(monkeypatch) -> None:
    expected = []
    for name in (
        "async_get_memory",
        "async_get_temporary_memory",
        "async_get_knowledge",
        "async_get_archive",
        "async_get_usage",
        "async_get_guest_mode",
        "async_get_request_rules",
    ):
        marker = object()
        expected.append(marker)
        monkeypatch.setattr(backup, name, AsyncMock(return_value=marker))

    result = await backup._managers(SimpleNamespace(), "entry", "agent")

    assert result == tuple(expected)


@pytest.mark.asyncio
async def test_snapshot_for_restore_validates_live_manager_backups(monkeypatch) -> None:
    payloads = {
        "memory": {"memories": []},
        "temporary": {"records": []},
        "knowledge": {"sources": []},
        "archive": {"sessions": [], "turns": []},
        "usage": {"totals": {}},
        "guest": {"schedule": None},
        "rules": {"storage_version": 1, "defaults": {}, "rules": []},
    }
    managers = tuple(
        SimpleNamespace(async_backup_data=AsyncMock(return_value=payloads[key]))
        for key in (
            "memory",
            "temporary",
            "knowledge",
            "archive",
            "usage",
            "guest",
            "rules",
        )
    )
    monkeypatch.setattr(
        backup.ConversationArchive,
        "validate_backup_data",
        staticmethod(lambda value, agent: ([(value, agent)], [])),
    )
    monkeypatch.setattr(
        backup.UsageManager,
        "validate_backup_data",
        staticmethod(lambda value, agent: (UsageTotals(), {agent: value}, [], [])),
    )
    monkeypatch.setattr(
        backup.GuestModeManager,
        "validate_backup_data",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        backup.RequestRules,
        "validate_backup_data",
        staticmethod(lambda value: value),
    )
    monkeypatch.setattr(
        backup.PersistentMemory,
        "validate_backup_data",
        staticmethod(lambda value: [value]),
    )
    monkeypatch.setattr(
        backup.TemporaryMemory,
        "validate_backup_data",
        staticmethod(lambda value: [value]),
    )
    monkeypatch.setattr(
        backup.KnowledgeLibrary,
        "validate_backup_data",
        staticmethod(lambda value: [value]),
    )
    monkeypatch.setattr(backup, "_integration_version", lambda: "9.9.9")
    subentry = SimpleNamespace(
        subentry_id="agent", title="Current", data=agent_config_defaults()
    )

    prepared = await backup._snapshot_for_restore(managers, subentry)

    assert prepared.title == "Current"
    assert prepared.integration_version == "9.9.9"
    assert prepared.memories == [payloads["memory"]]
    assert prepared.temporary_memories == [payloads["temporary"]]
    assert prepared.knowledge == [payloads["knowledge"]]
    assert prepared.archive_sessions == [(payloads["archive"], "agent")]
    assert prepared.usage_daily == {"agent": payloads["usage"]}
    assert prepared.guest_mode_schedule == payloads["guest"]
    assert prepared.request_rules == payloads["rules"]


@pytest.mark.asyncio
async def test_apply_restore_updates_retention_and_all_managers() -> None:
    names = ("memory", "temporary", "knowledge", "archive", "usage", "guest", "rules")
    managers = []
    for name in names:
        manager = SimpleNamespace(async_replace_backup=AsyncMock())
        if name == "usage":
            manager.request_retention_days = 0
            manager.run_retention_days = 0
        managers.append(manager)

    config = agent_config_defaults()
    config[backup.CONF_USAGE_REQUEST_RETENTION_DAYS] = 11
    config[backup.CONF_USAGE_RUN_RETENTION_DAYS] = 22
    prepared = _prepared(config=config)

    await backup._apply_restore(tuple(managers), prepared)

    memory, temporary, knowledge, archive, usage, guest, rules = managers
    assert usage.request_retention_days == 11
    assert usage.run_retention_days == 22
    memory.async_replace_backup.assert_awaited_once_with(prepared.memories)
    temporary.async_replace_backup.assert_awaited_once_with(prepared.temporary_memories)
    knowledge.async_replace_backup.assert_awaited_once_with(prepared.knowledge)
    archive.async_replace_backup.assert_awaited_once_with(
        prepared.archive_sessions, prepared.archive_turns
    )
    usage.async_replace_backup.assert_awaited_once_with(
        prepared.usage_totals,
        prepared.usage_daily,
        prepared.usage_requests,
        prepared.usage_runs,
    )
    guest.async_replace_backup.assert_awaited_once_with(prepared.guest_mode_schedule)
    rules.async_replace_backup.assert_awaited_once_with(prepared.request_rules)
