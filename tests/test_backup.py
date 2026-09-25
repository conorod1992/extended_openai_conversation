"""Tests for versioned per-agent full backup and replacement restore."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    BackupError,
    PreparedRestore,
    _safe_configuration,
    async_create_backup,
    async_restore_backup,
    inspect_backup,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_WORDING_GROUPS,
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    UsageTotals,
)
from homeassistant.util import dt as dt_util


class FakeStorage:
    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        self.data = deepcopy(data)


def _document() -> dict:
    now = dt_util.utcnow()
    config = agent_config_defaults()
    config["functions"] = [
        {
            "enabled": False,
            "spec": {
                "name": "lights",
                "description": "Control lights",
                "parameters": {"type": "object", "properties": {}},
            },
            "function": {"type": "native", "name": "execute_service"},
        }
    ]
    config["function_groups"] = [
        {
            "id": "lighting",
            "name": "Lighting",
            "description": "Lighting tools",
            "loading_mode": "on_demand",
            "functions": ["lights"],
        }
    ]
    totals = {
        "conversation_count": 4,
        "api_request_count": 5,
        "successful_request_count": 4,
        "failed_request_count": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cached_input_tokens": 30,
        "reasoning_tokens": 7,
        "details": {"input_cached_tokens": 30, "output_reasoning_tokens": 7},
    }
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": now.isoformat(),
        "integration_version": "4.6.0",
        "agent": {
            "title": "Jarvis",
            "source_entry_id": "entry-old",
            "source_subentry_id": "agent-old",
            "config": config,
        },
        "memories": {
            "memories": [
                {
                    "memory_id": "memory-1",
                    "user_id": "user-1",
                    "content": "The kitchen light is named Aurora",
                    "category": "home",
                    "source": "explicit",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            ]
        },
        "temporary_memories": {
            "records": [
                {
                    "memory_id": "active",
                    "scope_id": "user:user-1",
                    "content": "Guests arrive this evening",
                    "category": "plans",
                    "source": "automatic",
                    "expires_at": (now + timedelta(days=1)).isoformat(),
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
                {
                    "memory_id": "expired",
                    "scope_id": "user:user-1",
                    "content": "Yesterday's reminder",
                    "category": "plans",
                    "source": "automatic",
                    "expires_at": (now - timedelta(days=1)).isoformat(),
                    "created_at": (now - timedelta(days=2)).isoformat(),
                    "updated_at": (now - timedelta(days=2)).isoformat(),
                },
            ]
        },
        "knowledge": {
            "sources": [
                {
                    "source_id": "source-1",
                    "title": "House guide",
                    "description": "Local reference",
                    "content": "The stopcock is under the kitchen sink.",
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            ]
        },
        "archive": {"sessions": [], "turns": []},
        "usage": {
            "totals": totals,
            "daily": {},
            "requests": [
                {
                    "request_id": "request-1",
                    "run_id": "run-1",
                    "timestamp": now.isoformat(),
                    "agent_subentry_id": "agent-old",
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "api_mode": "responses",
                    "successful": True,
                    "duration_ms": 300,
                    "input_tokens": 25,
                    "output_tokens": 5,
                    "total_tokens": 30,
                    "cached_input_tokens": 8,
                    "reasoning_tokens": 2,
                    "request_stage": "initial",
                    "tool_calls_requested": 0,
                    "web_search_used": False,
                    "error_type": None,
                    "details": {"input_cached_tokens": 8},
                }
            ],
            "runs": [
                {
                    "run_id": "run-1",
                    "started_at": now.isoformat(),
                    "completed_at": now.isoformat(),
                    "duration_ms": 350,
                    "agent_subentry_id": "agent-old",
                    "home_assistant_conversation_id": "conversation-1",
                    "source_device_id": "device-1",
                    "request_count": 1,
                    "successful_request_count": 1,
                    "failed_request_count": 0,
                    "tool_call_count": 0,
                    "input_tokens": 25,
                    "output_tokens": 5,
                    "total_tokens": 30,
                    "cached_input_tokens": 8,
                    "reasoning_tokens": 2,
                    "successful": True,
                    "models": ["gpt-5-mini"],
                    "providers": ["openai"],
                    "api_modes": ["responses"],
                    "web_search_used": False,
                    "error_type": None,
                }
            ],
        },
        "request_rules": {
            "storage_version": 6,
            "defaults": {
                "word_forms": True,
                "wording_alternatives": True,
                "fuzzy": False,
                "fuzzy_threshold": 90,
            },
            "wording_groups": [
                {
                    "canonical": "activate",
                    "alternatives": ["power up"],
                }
            ],
            "groups": [{"id": "home", "name": "Home"}],
            "rules": [
                {
                    "id": "good-night",
                    "name": "Good night",
                    "enabled": True,
                    "phrases": ["good night"],
                    "match_type": "equals",
                    "action_type": "local_action",
                    "action": {
                        "actions": [
                            {
                                "domain": "script",
                                "service": "turn_on",
                                "target": {"entity_id": ["script.goodnight"]},
                                "data": {},
                            }
                        ],
                        "success_response": "Done",
                        "failure_response": "Failed safely",
                    },
                    "matching_behavior": "defaults",
                    "matching": {
                        "word_forms": True,
                        "wording_alternatives": True,
                        "fuzzy": False,
                        "fuzzy_threshold": 90,
                    },
                    "group_id": "home",
                    "order": 0,
                }
            ],
        },
    }


def test_full_backup_validation_preserves_durable_categories_and_expiry() -> None:
    document = _document()
    prepared = inspect_backup(document, "agent-new")

    tools = yaml.safe_load(prepared.config["functions"])
    assert tools[0]["enabled"] is False
    assert prepared.config["function_groups"][0]["id"] == "lighting"
    assert prepared.memories[0].content.endswith("Aurora")
    assert [record.memory_id for record in prepared.temporary_memories] == ["active"]
    assert (
        prepared.temporary_memories[0].expires_at
        == document["temporary_memories"]["records"][0]["expires_at"]
    )
    assert prepared.knowledge[0].content.startswith("The stopcock")
    assert prepared.usage_totals.total_tokens == 120
    assert prepared.usage_totals.cached_input_tokens == 30
    assert prepared.usage_totals.reasoning_tokens == 7
    assert prepared.usage_requests[0].agent_subentry_id == "agent-new"
    assert prepared.usage_requests[0].details["input_cached_tokens"] == 8
    assert prepared.usage_runs[0].agent_subentry_id == "agent-new"
    assert prepared.summary()["knowledge_sources"] == 1
    assert prepared.summary()["request_rules"] == 1
    assert prepared.request_rules["groups"] == [{"id": "home", "name": "Home"}]
    assert prepared.request_rules["wording_groups"] == [
        {"canonical": "activate", "alternatives": ["power up"]}
    ]
    assert prepared.request_rules["rules"][0]["group_id"] == "home"


def test_full_backup_inspection_accepts_quarantined_function_tool() -> None:
    document = _document()
    config = document["agent"]["config"]
    raw_tools = config["functions"]
    tools = raw_tools if isinstance(raw_tools, list) else yaml.safe_load(raw_tools)
    invalid = deepcopy(tools[0])
    invalid["spec"]["name"] = "unavailable_reminder"
    invalid["function"] = {"type": "native", "name": "reminders.unavailable"}
    config["functions"] = yaml.safe_dump(
        [tools[0], invalid], sort_keys=False, allow_unicode=True
    )
    config["function_groups"] = [
        {
            "id": "mixed",
            "name": "Mixed",
            "description": "Contains one quarantined Function Tool",
            "loading_mode": "always",
            "functions": [tools[0]["spec"]["name"], "unavailable_reminder"],
        }
    ]

    prepared = inspect_backup(document, "agent-new")

    assert prepared.config["functions"] == config["functions"]
    assert prepared.config["function_groups"] == config["function_groups"]


def test_backup_rejects_malformed_and_newer_versions() -> None:
    malformed = _document()
    malformed["memories"]["memories"][0]["content"] = 42
    with pytest.raises(BackupError, match="incomplete or corrupted"):
        inspect_backup(malformed, "agent-new")

    newer = _document()
    newer["version"] = BACKUP_VERSION + 1
    with pytest.raises(BackupError, match="newer unsupported"):
        inspect_backup(newer, "agent-new")


def test_version_two_backup_migrates_with_empty_request_rules() -> None:
    legacy = _document()
    legacy["version"] = 2
    legacy.pop("request_rules")

    prepared = inspect_backup(legacy, "agent-new")

    assert prepared.request_rules["rules"] == []
    assert prepared.request_rules["groups"] == []
    assert prepared.request_rules["wording_groups"] == list(DEFAULT_WORDING_GROUPS)


def test_pre_group_request_rules_backup_adds_current_additive_defaults() -> None:
    legacy = _document()
    legacy["version"] = 3
    request_rules = legacy["request_rules"]
    request_rules["storage_version"] = 1
    request_rules.pop("groups")
    request_rules.pop("wording_groups")
    request_rules["rules"][0].pop("group_id")

    prepared = inspect_backup(legacy, "agent-new")

    assert prepared.request_rules["groups"] == []
    assert prepared.request_rules["wording_groups"] == list(DEFAULT_WORDING_GROUPS)
    assert prepared.request_rules["rules"][0]["id"] == "good-night"
    assert prepared.request_rules["rules"][0]["group_id"] is None


def test_version_four_backup_ignores_only_retired_section() -> None:
    legacy = _document()
    legacy["version"] = 4
    legacy["protected_actions"] = {
        "storage_version": 1,
        "pin_hash": None,
        "rules": [],
    }

    prepared = inspect_backup(legacy, "agent-new")

    assert prepared.request_rules["rules"][0]["id"] == "good-night"
    assert "protected_actions" not in prepared.summary()


def test_version_four_backup_rejects_unknown_top_level_section() -> None:
    legacy = _document()
    legacy["version"] = 4
    legacy["unexpected_section"] = {"value": "not allowed"}

    with pytest.raises(BackupError, match="incomplete or corrupted"):
        inspect_backup(legacy, "agent-new")


def test_current_backup_rejects_retired_or_unknown_top_level_sections() -> None:
    current = _document()
    current["protected_actions"] = {"rules": []}

    with pytest.raises(BackupError, match="incomplete or corrupted"):
        inspect_backup(current, "agent-new")


def test_backup_secret_redaction_preserves_schema_property_names() -> None:
    safe = _safe_configuration(
        {
            "functions": [
                {
                    "function": {
                        "api_key": "secret",
                        "headers": {"Authorization": "Bearer secret"},
                        "example": "sk-1234567890abcdef",
                    },
                    "spec": {
                        "parameters": {"properties": {"password": {"type": "string"}}}
                    },
                }
            ]
        }
    )
    serialized = str(safe)
    assert "Bearer secret" not in serialized
    assert "sk-1234567890abcdef" not in serialized
    assert "password" in safe["functions"][0]["spec"]["parameters"]["properties"]


async def test_create_full_backup_contains_only_durable_safe_state(
    monkeypatch, hass
) -> None:
    source = _document()
    config = source["agent"]["config"]
    config["prompt"] = "Never reveal sk-1234567890abcdef"
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1", title="Jarvis", data=config)
    for getter, section in (
        ("async_get_memory", "memories"),
        ("async_get_temporary_memory", "temporary_memories"),
        ("async_get_knowledge", "knowledge"),
        ("async_get_archive", "archive"),
        ("async_get_usage", "usage"),
        ("async_get_request_rules", "request_rules"),
    ):
        monkeypatch.setattr(
            f"custom_components.extended_openai_conversation_responses.backup.{getter}",
            AsyncMock(
                return_value=SimpleNamespace(
                    async_backup_data=AsyncMock(return_value=source[section])
                )
            ),
        )

    result = await async_create_backup(hass, entry, subentry)

    document = result["document"]
    assert document["format"] == BACKUP_FORMAT
    assert document["agent"]["config"]["function_groups"][0]["id"] == "lighting"
    assert document["memories"]["memories"][0]["memory_id"] == "memory-1"
    assert document["request_rules"]["groups"] == [{"id": "home", "name": "Home"}]
    assert document["request_rules"]["wording_groups"] == [
        {"canonical": "activate", "alternatives": ["power up"]}
    ]
    assert document["request_rules"]["rules"][0]["group_id"] == "home"
    assert "sk-1234567890abcdef" not in result["json"]
    assert "loaded_function_groups" not in result["json"]
    assert result["filename"].startswith("jarvis-full-backup-")


async def test_replace_helpers_rebuild_canonical_state() -> None:
    document = _document()
    memory_records = PersistentMemory.validate_backup_data(document["memories"])
    temporary_records = TemporaryMemory.validate_backup_data(
        document["temporary_memories"]
    )
    knowledge_sources = KnowledgeLibrary.validate_backup_data(document["knowledge"])
    usage = UsageManager.validate_backup_data(document["usage"], "agent-new")
    assert len(memory_records) == 1
    assert len(temporary_records) == 1
    assert len(knowledge_sources) == 1
    assert usage[0].details["input_cached_tokens"] == 30

    memory = PersistentMemory(FakeStorage({"memories": []}))
    await memory.async_initialize()
    await memory.async_replace_backup(memory_records)
    memory_backup = await memory.async_backup_data()
    original = document["memories"]["memories"][0]
    assert memory_backup["memories"][0] == {
        **original,
        "importance": "normal",
        "subject": None,
        "key": None,
        "valid_from": None,
        "last_confirmed_at": original["updated_at"],
    }

    temporary = TemporaryMemory(FakeStorage({"records": []}))
    await temporary.async_initialize()
    await temporary.async_replace_backup(temporary_records)
    assert (await temporary.async_backup_data())["records"][0]["memory_id"] == "active"

    knowledge = KnowledgeLibrary(FakeStorage({"sources": []}))
    await knowledge.async_initialize()
    await knowledge.async_replace_backup(knowledge_sources)
    knowledge_backup = await knowledge.async_backup_data()
    assert knowledge_backup["sources"][0] == {
        **document["knowledge"]["sources"][0],
        "enabled": True,
    }

    usage_manager = UsageManager(FakeStorage(), FakeStorage(), FakeStorage())
    await usage_manager.async_initialize()
    await usage_manager.async_replace_backup(*usage)
    assert usage_manager.as_dict()["total_tokens"] == 120

    request_rules = RequestRules(FakeStorage())
    await request_rules.async_initialize()
    await request_rules.async_replace_backup(document["request_rules"])
    restored_rules = await request_rules.async_backup_data()
    assert restored_rules["groups"] == [{"id": "home", "name": "Home"}]
    assert restored_rules["wording_groups"] == [
        {"canonical": "activate", "alternatives": ["power up"]}
    ]
    assert restored_rules["rules"][0]["group_id"] == "home"


async def test_restore_failure_rolls_back_before_reporting(monkeypatch, hass) -> None:
    _journal_fixture(monkeypatch)
    prepared = inspect_backup(_document(), "agent-new")
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-new", title="Current", data=agent_config_defaults()
    )
    managers = (object(), object(), object(), object(), object())
    apply = AsyncMock(side_effect=[RuntimeError("write failed"), None])
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.restore_recovery._durable_managers",
        AsyncMock(return_value=managers),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.backup._snapshot_for_restore",
        AsyncMock(return_value=deepcopy(prepared)),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.restore_recovery._apply_prepared",
        apply,
    )

    with pytest.raises(BackupError, match="previous agent state was recovered"):
        await async_restore_backup(hass, entry, subentry, _document())
    assert apply.await_count == 2


async def test_unsupported_restore_does_not_access_agent_stores(
    monkeypatch, hass
) -> None:
    document = _document()
    document["version"] = BACKUP_VERSION + 1
    managers = AsyncMock()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.restore_recovery._durable_managers",
        managers,
    )

    with pytest.raises(BackupError, match="newer unsupported"):
        await async_restore_backup(
            hass,
            SimpleNamespace(entry_id="entry-1"),
            SimpleNamespace(
                subentry_id="agent-1", title="Current", data=agent_config_defaults()
            ),
            document,
        )
    managers.assert_not_awaited()


def _journal_fixture(monkeypatch):
    from tests.test_restore_recovery import MemoryJournalStore

    store = MemoryJournalStore()
    monkeypatch.setattr(restore_recovery, "_journal_store", lambda *_: store)
    monkeypatch.setattr(restore_recovery, "_async_persist_config_entries", AsyncMock())
    monkeypatch.setattr(restore_recovery, "reset_restored_runtime", lambda *_: None)


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


@pytest.mark.parametrize(
    "invalid_limit", [True, 0, -1, 1.5, backup.MAX_BACKUP_BYTES + 1]
)
def test_inspect_backup_rejects_invalid_byte_limits(invalid_limit) -> None:
    with pytest.raises(ValueError, match="byte limit is invalid"):
        backup.inspect_backup({}, "agent", max_bytes=invalid_limit)


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


async def test_inspect_backup_wraps_nested_validator_errors(monkeypatch) -> None:
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


async def test_inspect_backup_validates_optional_guest_mode(monkeypatch) -> None:
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
    _journal_fixture(monkeypatch)
    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(
        subentry_id="agent", title="Current", data=agent_config_defaults()
    )
    prepared = _prepared()
    rollback = _prepared()
    monkeypatch.setattr(backup, "inspect_backup", lambda _value, _agent: prepared)
    monkeypatch.setattr(
        restore_recovery, "_durable_managers", AsyncMock(return_value=(object(),))
    )
    monkeypatch.setattr(
        backup, "_snapshot_for_restore", AsyncMock(return_value=rollback)
    )
    monkeypatch.setattr(
        restore_recovery,
        "_apply_prepared",
        AsyncMock(
            side_effect=[
                RuntimeError("restore failed"),
                RuntimeError("rollback failed"),
            ]
        ),
    )

    with pytest.raises(BackupError, match="recovery is still pending"):
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
