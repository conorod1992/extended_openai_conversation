"""Tests for versioned per-agent full backup and replacement restore."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    BackupError,
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
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.usage import UsageManager
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
            "storage_version": 1,
            "defaults": {
                "word_forms": True,
                "wording_alternatives": True,
                "fuzzy": False,
                "fuzzy_threshold": 90,
            },
            "rules": [],
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
    assert prepared.summary()["request_rules"] == 0


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
    assert (await knowledge.async_backup_data()) == document["knowledge"]

    usage_manager = UsageManager(FakeStorage(), FakeStorage(), FakeStorage())
    await usage_manager.async_initialize()
    await usage_manager.async_replace_backup(*usage)
    assert usage_manager.as_dict()["total_tokens"] == 120

    request_rules = RequestRules(FakeStorage())
    await request_rules.async_initialize()
    await request_rules.async_replace_backup(document["request_rules"])
    assert (await request_rules.async_backup_data())["rules"] == []


async def test_restore_failure_rolls_back_before_reporting(monkeypatch, hass) -> None:
    prepared = inspect_backup(_document(), "agent-new")
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(
        subentry_id="agent-new", title="Current", data=agent_config_defaults()
    )
    managers = (object(), object(), object(), object(), object())
    apply = AsyncMock(side_effect=[RuntimeError("write failed"), None])
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.backup._managers",
        AsyncMock(return_value=managers),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.backup._snapshot_for_restore",
        AsyncMock(return_value=deepcopy(prepared)),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.backup._apply_restore",
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
        "custom_components.extended_openai_conversation_responses.backup._managers",
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
