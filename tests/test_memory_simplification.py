"""Removed metadata compatibility and explicit short-term management contracts."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryStore,
    PersistentMemory,
    memory_tools,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util


class Storage:
    def __init__(self, data=None):
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(dt_util, "utcnow", lambda: datetime(2026, 9, 1, tzinfo=UTC))


def legacy_record(memory_id="a"):
    return {
        "memory_id": memory_id, "user_id": "alice", "content": "Tea is in the pantry",
        "category": "home", "source": "explicit", "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "importance": {"obsolete": True},
        "last_confirmed_at": ["invalid old value"],
    }


@pytest.mark.parametrize("version", [0, 1])
async def test_old_store_migrations_drop_removed_fields(version):
    data = await MemoryStore.__new__(MemoryStore)._async_migrate_func(
        version, 0, {"memories": [legacy_record()]}
    )
    assert "importance" not in data["memories"][0]
    assert "last_confirmed_at" not in data["memories"][0]


async def test_current_store_and_backup_ignore_any_legacy_values():
    storage = Storage({"memories": [legacy_record()]})
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    record = (await memory.async_list("alice"))[0]
    assert record.memory_id == "a"
    assert not hasattr(record, "importance")
    assert not hasattr(record, "last_confirmed_at")
    assert set(storage.data["memories"][0]).isdisjoint({"importance", "last_confirmed_at"})
    restored = PersistentMemory.validate_backup_data({"memories": [legacy_record()]})
    assert restored == [record]
    assert (await memory.async_backup_data()) == storage.data


@pytest.mark.parametrize("hybrid", [False, True])
async def test_retrieval_ties_use_id_and_ignore_old_metadata_and_edits(hybrid):
    records = [legacy_record("z"), legacy_record("a")]
    records[0].update(importance="high", last_confirmed_at="2099-01-01T00:00:00Z", updated_at="2099-01-01T00:00:00Z")
    memory = PersistentMemory(Storage({"memories": records}))
    await memory.async_initialize()
    memory.set_embedding_provider(AsyncMock(return_value=[[1.0, 0.0]] * 2))
    if hybrid:
        # Prepare document vectors separately from the single query vector.
        memory.set_embedding_provider(AsyncMock(side_effect=lambda values: [[1.0, 0.0] for _ in values]))
        await memory.async_prepare_hybrid(["alice"], "pantry")
    async def ranked():
        return [record.memory_id for record in await memory.async_search(
            "alice", "pantry", hybrid=hybrid, query_embedding=[1.0, 0.0]
        )]
    assert await ranked() == ["a", "z"]
    await memory.async_update("alice", "z", valid_from="2026-08-01T00:00:00Z")
    assert await ranked() == ["a", "z"]


def test_model_and_management_schemas_have_no_removed_controls():
    from custom_components.extended_openai_conversation_responses.const import MEMORY_PROMPT
    from custom_components.extended_openai_conversation_responses.model_payload import PERSISTENT_MEMORY_GUIDANCE
    for guidance in (MEMORY_PROMPT, PERSISTENT_MEMORY_GUIDANCE):
        assert "importance" not in guidance.lower()
        assert "confirmed" not in guidance.lower()
    for tool in memory_tools():
        properties = tool["spec"]["parameters"]["properties"]
        assert set(properties).isdisjoint({"importance", "last_confirmed_at", "refresh_confirmation"})
    import voluptuous as vol
    for field, value in [("importance", "high"), ("refresh_confirmation", True)]:
        with pytest.raises(vol.Invalid):
            management_ui.websocket_management._ws_schema({
                "id": 1, "type": "extended_openai_conversation_responses/management",
                "section": "memories", "action": "add", field: value,
            })
    payload = {
        "id": 1, "type": "extended_openai_conversation_responses/management",
        "section": "memories", "action": "temporary_add", "entry_id": "entry",
        "subentry_id": "agent", "scope_id": "user:alice", "target_scope_id": "user:alice",
        "content": "A manual fact", "category": "general", "expires_at": "2026-09-02T12:00:00Z",
    }
    assert management_ui.websocket_management._ws_schema(payload) == payload


async def temporary_command(manager, monkeypatch, action="temporary_add", *, admin=False, **values):
    monkeypatch.setattr(management_ui, "async_get_temporary_memory", AsyncMock(return_value=manager))
    request = management_ui._ManagementRequest(
        SimpleNamespace(), "alice", admin,
        {"section": "memories", "action": action, **values}, "entry", "agent",
        SimpleNamespace(entry_id="entry"), SimpleNamespace(subentry_id="agent", data={}),
    )
    return await management_ui.async_memories_command(request)


async def test_temporary_add_manual_privacy_ownership_and_fresh_manager(monkeypatch):
    storage = Storage()
    manager = TemporaryMemory(storage)
    await manager.async_initialize()
    result = await temporary_command(manager, monkeypatch, content="Health condition is asthma",
        category="health", expires_at="2026-09-02T12:30:00+01:00")
    assert result["status"] == "created"
    assert result["scope_id"] == "user:alice"
    assert result["memory"]["source"] == "manual"
    assert result["memory"]["expires_at"] == "2026-09-02T11:30:00+00:00"
    assert result["memory"]["owner_scope_id"] == "user:alice"
    fresh = TemporaryMemory(storage)
    await fresh.async_initialize()
    records = await fresh.async_list_owned("user:alice")
    assert len(records) == 1 and records[0].memory_id == result["memory"]["memory_id"]
    assert records[0].source == "manual"
    await fresh.async_update_owned("user:alice", records[0].memory_id, "Health condition is hay fever", None, None)
    assert TemporaryMemory.validate_backup_data(await fresh.async_backup_data())[0].source == "manual"


async def test_automatic_creation_keeps_source_privacy_and_duplicate_behavior():
    manager = TemporaryMemory(Storage())
    await manager.async_initialize()
    first = await manager.async_add("conversation:one", "Delivery is due today", "2026-09-02T12:00:00Z", owner_scope_id="user:alice")
    second = await manager.async_add("conversation:two", "Delivery is due today", "2026-09-03T12:00:00Z", owner_scope_id="user:alice")
    assert first["status"] == "created" and second["status"] == "updated"
    assert first["memory"]["memory_id"] == second["memory"]["memory_id"]
    assert second["memory"]["source"] == "automatic"
    assert len(await manager.async_list_owned("user:alice")) == 1
    with pytest.raises(ValueError, match="explicit"):
        await manager.async_add("conversation:one", "Health condition is asthma", "2026-09-02T12:00:00Z", owner_scope_id="user:alice")
    with pytest.raises(ValueError, match="explicit"):
        await manager.async_update_owned("user:alice", first["memory"]["memory_id"], "Health condition is asthma", None, None)


@pytest.mark.parametrize("manual", [False, True])
async def test_short_term_secret_rejection_applies_to_both_sources(manual):
    manager = TemporaryMemory(Storage())
    await manager.async_initialize()
    with pytest.raises(ValueError, match="secret"):
        await manager.async_add("user:alice", "Password is hunter2", "2026-09-02T12:00:00Z", owner_scope_id="user:alice", source="manual" if manual else "automatic")
    assert not await manager.async_list_owned("user:alice")


@pytest.mark.parametrize("expiry", ["invalid", "2026-09-02T12:00:00", "2026-08-31T12:00:00Z", "2028-09-02T12:00:00Z"])
async def test_manual_expiry_validation_rejects_invalid_naive_past_and_over_year(expiry):
    manager = TemporaryMemory(Storage())
    with pytest.raises(ValueError):
        await manager.async_add_owned("user:alice", "A fact", expiry)
    assert not await manager.async_list_owned("user:alice")


@pytest.mark.parametrize("target", ["user:bob", "shared:household", "device:kitchen", "conversation:one", "__anonymous__"])
async def test_manual_creation_cannot_choose_unauthorized_owner(monkeypatch, target):
    manager = TemporaryMemory(Storage())
    with pytest.raises(HomeAssistantError):
        await temporary_command(manager, monkeypatch, target_scope_id=target, content="A fact", expires_at="2026-09-02T12:00:00Z")
    assert not await manager.async_list_owned("user:alice")


async def test_manual_shared_creation_and_write_failure_rollback(monkeypatch):
    storage = Storage()
    manager = TemporaryMemory(storage)
    result = await temporary_command(manager, monkeypatch, admin=True, target_scope_id="shared:household", content="Delivery is due today", expires_at="2026-09-02T12:00:00Z")
    assert result["memory"]["owner_scope_id"] == "shared:household"
    before = await manager.async_backup_data()
    storage.async_save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        await temporary_command(manager, monkeypatch, content="Another fact", expires_at=(dt_util.utcnow() + timedelta(days=1)).isoformat())
    assert await manager.async_backup_data() == before
