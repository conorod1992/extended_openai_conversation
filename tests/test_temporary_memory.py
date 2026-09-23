"""Temporary-memory lifecycle, ownership, and safety tests."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import temporary_memory
from custom_components.extended_openai_conversation_responses.const import (
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_TEMPORARY_SCOPE,
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.scope import unretained_scope
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_CONTENT_LENGTH,
    MAX_INJECT_CHARACTERS,
    MAX_INJECT_RECORDS,
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    TemporaryMemoryRecord,
    temporary_memory_tools,
)
from homeassistant.util import dt as dt_util


class Storage:
    def __init__(self, data=None):
        self.data = data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def future(hours=1) -> str:
    return (dt_util.utcnow() + timedelta(hours=hours)).isoformat()


def stored_record(
    index: int,
    *,
    scope_id: str = "device:kitchen",
    owner_scope_id: str | None = "user:alice",
    content_length: int = 32,
) -> dict:
    now = dt_util.utcnow() + timedelta(seconds=index)
    return {
        "memory_id": f"memory-{index:03d}",
        "scope_id": scope_id,
        "owner_scope_id": owner_scope_id,
        "content": f"fact-{index:03d}-" + ("x" * content_length),
        "category": "general",
        "source": "automatic",
        "expires_at": future(24),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


async def test_active_records_are_owned_persisted_and_bounded() -> None:
    storage = Storage()
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    created = await memory.async_add(
        "device:kitchen",
        "Cooking pasta",
        future(),
        "activity",
        owner_scope_id="user:alice",
    )
    assert [
        item.content
        for item in await memory.async_active(
            "device:kitchen", owner_scope_id="user:alice"
        )
    ] == ["Cooking pasta"]
    assert await memory.async_active("device:kitchen", owner_scope_id="user:bob") == []

    restored = TemporaryMemory(Storage(storage.data))
    await restored.async_initialize()
    assert (
        len(
            await restored.async_active(
                "conversation:fresh", owner_scope_id="user:alice"
            )
        )
        == 1
    )
    assert (
        await restored.async_delete(
            "conversation:other",
            [created["memory"]["memory_id"]],
            owner_scope_id="user:bob",
        )
        == 0
    )


async def test_same_continuity_scope_is_isolated_by_resolved_owner() -> None:
    memory = TemporaryMemory(Storage())
    await memory.async_initialize()

    alice = await memory.async_add(
        "device:kitchen",
        "Alice is waiting for a parcel",
        future(),
        "delivery",
        owner_scope_id="user:alice",
    )
    bob = await memory.async_add(
        "device:kitchen",
        "Bob is cooking pasta",
        future(),
        "activity",
        owner_scope_id="user:bob",
    )

    assert [
        item.content
        for item in await memory.async_active(
            "conversation:new-alice", owner_scope_id="user:alice"
        )
    ] == ["Alice is waiting for a parcel"]
    assert [
        item.content
        for item in await memory.async_active(
            "conversation:new-bob", owner_scope_id="user:bob"
        )
    ] == ["Bob is cooking pasta"]

    with pytest.raises(ValueError, match="temporary memory not found"):
        await memory.async_update(
            "conversation:new-alice",
            bob["memory"]["memory_id"],
            "Alice changed Bob's fact",
            None,
            None,
            owner_scope_id="user:alice",
        )
    assert (
        await memory.async_delete(
            "conversation:new-bob",
            [alice["memory"]["memory_id"]],
            owner_scope_id="user:bob",
        )
        == 0
    )


async def test_missing_or_invalid_owner_never_becomes_a_wildcard() -> None:
    memory = TemporaryMemory(Storage())
    await memory.async_initialize()

    assert await memory.async_active("device:kitchen") == []
    with pytest.raises(ValueError, match="resolved Personal or Shared owner"):
        await memory.async_add("device:kitchen", "Cooking pasta", future())
    with pytest.raises(ValueError, match="resolved Personal or Shared owner"):
        await memory.async_add(
            "device:kitchen",
            "Cooking pasta",
            future(),
            owner_scope_id="device:kitchen",
        )


@pytest.mark.parametrize(
    ("scope_id", "expected_owner"),
    [
        ("user:alice", "user:alice"),
        ("shared:household", "shared:household"),
    ],
)
async def test_canonical_legacy_scope_gains_matching_owner_on_load(
    scope_id: str, expected_owner: str
) -> None:
    record = stored_record(1, scope_id=scope_id, owner_scope_id=None)
    memory = TemporaryMemory(Storage({"records": [record]}))
    await memory.async_initialize()

    active = await memory.async_active(
        "conversation:fresh", owner_scope_id=expected_owner
    )
    assert [item.memory_id for item in active] == ["memory-001"]
    assert active[0].owner_scope_id == expected_owner


async def test_legacy_device_scope_and_invalid_explicit_owner_are_removed() -> None:
    legacy_device = stored_record(1, owner_scope_id=None)
    invalid_owner = stored_record(
        2, scope_id="user:alice", owner_scope_id="device:kitchen"
    )
    storage = Storage({"records": [legacy_device, invalid_owner]})
    memory = TemporaryMemory(storage)
    await memory.async_initialize()

    assert await memory.async_list_owned("user:alice") == []
    assert storage.data == {"records": []}
    assert memory.stats()["invalid_owner_records_pruned"] == 2


async def test_expiry_pruned_at_startup_and_before_injection() -> None:
    expired = stored_record(1, scope_id="user:alice", owner_scope_id="user:alice")
    expired["expires_at"] = (dt_util.utcnow() - timedelta(minutes=1)).isoformat()
    memory = TemporaryMemory(Storage({"records": [expired]}))
    await memory.async_initialize()
    assert await memory.async_active("user:alice", owner_scope_id="user:alice") == []
    assert memory.expired_pruned == 1


async def test_update_supersedes_and_secret_is_rejected() -> None:
    memory = TemporaryMemory(Storage())
    await memory.async_initialize()
    created = await memory.async_add(
        "user:alice",
        "Parents are visiting this weekend",
        future(24),
        "visitors",
        owner_scope_id="user:alice",
    )
    updated = await memory.async_update(
        "conversation:fresh",
        created["memory"]["memory_id"],
        "Parents are visiting next weekend",
        future(48),
        "family",
        owner_scope_id="user:alice",
    )
    assert updated.content.endswith("next weekend")
    assert updated.category == "family"
    assert dt_util.parse_datetime(updated.expires_at) > dt_util.utcnow()
    with pytest.raises(ValueError):
        await memory.async_add(
            "user:alice",
            "My PIN is 1234",
            future(),
            owner_scope_id="user:alice",
        )


async def test_management_list_is_not_limited_by_model_injection_selection() -> None:
    records = [stored_record(index, content_length=180) for index in range(40)]
    memory = TemporaryMemory(Storage({"records": records}))
    await memory.async_initialize()

    injected = await memory.async_active(
        "conversation:one", owner_scope_id="user:alice"
    )
    managed = await memory.async_list_owned("user:alice")

    assert len(injected) <= MAX_INJECT_RECORDS
    assert len(managed) == 40
    assert sum(len(item.content) for item in managed) > 6000


async def test_startup_enforces_existing_record_ceiling_with_diagnostics() -> None:
    records = [stored_record(index) for index in range(MAX_ACTIVE_RECORDS + 5)]
    storage = Storage({"records": records})
    memory = TemporaryMemory(storage)
    await memory.async_initialize()

    managed = await memory.async_list_owned("user:alice")
    assert len(managed) == MAX_ACTIVE_RECORDS
    assert memory.stats()["startup_overflow_records_pruned"] == 5
    assert len(storage.data["records"]) == MAX_ACTIVE_RECORDS
    assert "memory-104" in {record.memory_id for record in managed}
    assert "memory-000" not in {record.memory_id for record in managed}


async def test_backup_restore_drops_unprovable_legacy_owner() -> None:
    good = stored_record(1, scope_id="shared:household", owner_scope_id=None)
    bad = stored_record(2, owner_scope_id=None)
    validated = TemporaryMemory.validate_backup_data({"records": [good, bad]})

    assert len(validated) == 1
    assert validated[0].owner_scope_id == "shared:household"

    memory = TemporaryMemory(Storage())
    await memory.async_initialize()
    await memory.async_replace_backup(validated)
    assert len(await memory.async_list_owned("shared:household")) == 1


def test_balanced_prompt_infers_weekend_expiry_without_clarification() -> None:
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.hass = MagicMock()
    entity.hass.config.time_zone = "Europe/Dublin"
    entity.skill_manager = SimpleNamespace(get_all_skills=lambda: [])
    entity.subentry = SimpleNamespace(
        data={
            CONF_PROMPT: "Base prompt",
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        }
    )
    prompt = entity._build_system_prompt(
        [],
        SimpleNamespace(device_id=None),
        SimpleNamespace(extra_system_prompt=None),
    )
    assert "instead of asking unnecessary clarification" in prompt
    assert 'for "this weekend" use the end of Sunday' in prompt
    assert "Europe/Dublin" in prompt


# Canonical validation, serialization, cold-read, and manager-cache coverage.

class ValidationStorage:
    """Small detached storage double."""

    def __init__(self, data: Any = None) -> None:
        self.data = deepcopy(data)
        self.save_count = 0

    async def async_load(self) -> Any:
        return deepcopy(self.data)

    async def async_save(self, data: Any) -> None:
        self.data = deepcopy(data)
        self.save_count += 1


def _future(*, days: int = 0, hours: int = 1) -> str:
    return (dt_util.utcnow() + timedelta(days=days, hours=hours)).isoformat()


def _record(
    memory_id: str = "memory-1",
    *,
    content: str = "A useful temporary fact",
    scope_id: str = "conversation:kitchen",
    owner_scope_id: str | None = "user:alice",
    expires_at: str | None = None,
) -> dict[str, Any]:
    now = dt_util.utcnow().isoformat()
    return {
        "memory_id": memory_id,
        "scope_id": scope_id,
        "owner_scope_id": owner_scope_id,
        "content": content,
        "category": "general",
        "source": "automatic",
        "expires_at": expires_at or _future(),
        "created_at": now,
        "updated_at": now,
    }


def _model_record(
    memory_id: str, content: str, *, updated_offset: int = 0
) -> TemporaryMemoryRecord:
    raw = _record(memory_id, content=content)
    raw["updated_at"] = (
        dt_util.utcnow() + timedelta(seconds=updated_offset)
    ).isoformat()
    return TemporaryMemoryRecord(**raw)


async def test_initialize_skips_malformed_records_and_keeps_valid_data() -> None:
    storage = ValidationStorage({"records": [None, {"memory_id": "broken"}, _record()]})
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]

    await manager.async_initialize()

    assert [item.memory_id for item in manager._records.values()] == ["memory-1"]


async def test_add_coalesces_duplicate_for_same_owner_and_persists_update() -> None:
    storage = ValidationStorage()
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]
    await manager.async_initialize()
    created = await manager.async_add(
        "conversation:first",
        "Parcel arrives tomorrow",
        _future(),
        "delivery",
        owner_scope_id="user:alice",
    )

    updated = await manager.async_add(
        "conversation:first",
        "PARCEL ARRIVES TOMORROW",
        _future(hours=2),
        "schedule",
        owner_scope_id="user:alice",
    )

    assert updated["status"] == "updated"
    assert updated["memory"]["memory_id"] == created["memory"]["memory_id"]
    assert updated["memory"]["category"] == "schedule"
    assert len(manager._records) == 1
    assert storage.save_count == 2


def test_injection_budget_skips_large_later_record_but_keeps_smaller_one() -> None:
    records = [
        _model_record("first", "a" * (MAX_INJECT_CHARACTERS - 5), updated_offset=3),
        _model_record("too-large", "b" * 10, updated_offset=2),
        _model_record("fits", "c" * 5, updated_offset=1),
    ]

    selected = TemporaryMemory.select_active_snapshot(
        records, "conversation:kitchen", owner_scope_id="user:alice"
    )

    assert [record.memory_id for record in selected] == ["first", "fits"]


async def test_delete_owned_records_deduplicates_ids_and_saves_once() -> None:
    storage = ValidationStorage({"records": [_record()]})
    manager = TemporaryMemory(storage)  # type: ignore[arg-type]
    await manager.async_initialize()

    deleted = await manager.async_delete(
        "conversation:kitchen",
        ["memory-1", "memory-1", "missing"],
        owner_scope_id="user:alice",
    )

    assert deleted == 1
    assert storage.data == {"records": []}
    assert storage.save_count == 1


def test_scope_counts_reports_records_by_continuity_scope() -> None:
    manager = TemporaryMemory(ValidationStorage())  # type: ignore[arg-type]
    manager._records = {
        "one": TemporaryMemoryRecord(**_record("one", scope_id="conversation:one")),
        "two": TemporaryMemoryRecord(**_record("two", scope_id="conversation:one")),
        "three": TemporaryMemoryRecord(**_record("three", scope_id="conversation:two")),
    }

    assert manager.scope_counts() == {"conversation:one": 2, "conversation:two": 1}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "incomplete or corrupted"),
        ({"unexpected": []}, "incomplete or corrupted"),
        ({"records": ()}, "count is invalid"),
        ({"records": [None]}, "record must be an object"),
        ({"records": [{"memory_id": "incomplete"}]}, "record is invalid"),
    ],
)
def test_backup_validation_rejects_invalid_container_shapes(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        TemporaryMemory.validate_backup_data(payload)


def test_backup_validation_rejects_too_many_records() -> None:
    with pytest.raises(ValueError, match="count is invalid"):
        TemporaryMemory.validate_backup_data(
            {
                "records": [
                    _record(str(index)) for index in range(MAX_ACTIVE_RECORDS + 1)
                ]
            }
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("content", 1, "fields must be strings"),
        ("owner_scope_id", 1, "owner must be a string"),
        ("memory_id", "", "metadata is invalid"),
        ("memory_id", "x" * 129, "metadata is invalid"),
        ("scope_id", "", "metadata is invalid"),
        ("scope_id", "x" * 129, "metadata is invalid"),
        ("owner_scope_id", "", "metadata is invalid"),
        ("owner_scope_id", "x" * 129, "metadata is invalid"),
        ("source", "explicit", "metadata is invalid"),
        ("content", "", "content must contain"),
        ("content", "x" * (MAX_CONTENT_LENGTH + 1), "content must contain"),
        ("category", "", "category must contain"),
        ("created_at", "invalid", "timestamp is invalid"),
        ("updated_at", "invalid", "timestamp is invalid"),
    ],
)
def test_backup_validation_rejects_invalid_record_fields(field, value, message) -> None:
    raw = _record()
    raw[field] = value

    with pytest.raises(ValueError, match=message):
        TemporaryMemory.validate_backup_data({"records": [raw]})


def test_backup_validation_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="metadata is invalid"):
        TemporaryMemory.validate_backup_data(
            {"records": [_record(), _record(content="Different fact")]}
        )


def test_backup_validation_drops_expired_record() -> None:
    expired = _record(expires_at=(dt_util.utcnow() - timedelta(seconds=1)).isoformat())

    assert TemporaryMemory.validate_backup_data({"records": [expired]}) == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("not-a-date", "ISO date-time"),
        ("2026-09-13T12:00:00", "include a timezone"),
    ],
)
def test_parse_expiry_requires_valid_aware_datetime(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        temporary_memory._parse_expiry(value)


def test_future_expiry_rejects_past_and_overlong_retention() -> None:
    with pytest.raises(ValueError, match="must be in the future"):
        temporary_memory._parse_future_expiry(
            (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
        )
    with pytest.raises(ValueError, match="longer than one year"):
        temporary_memory._parse_future_expiry(_future(days=367))


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (1, "must be a string"),
        ("   ", "must contain"),
        ("too long", "must contain"),
    ],
)
def test_clean_rejects_invalid_values(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        temporary_memory._clean(value, 3, "field")


def test_serialization_optionally_includes_continuity_scope() -> None:
    record = TemporaryMemoryRecord(**_record())

    assert "scope_id" not in temporary_memory.temporary_memory_as_dict(record)
    assert (
        temporary_memory.temporary_memory_as_dict(record, include_scope=True)[
            "scope_id"
        ]
        == record.scope_id
    )


def test_store_factory_uses_private_atomic_non_loop_serialization(monkeypatch) -> None:
    captured = SimpleNamespace(args=None, kwargs=None)

    class FakeStore:
        def __init__(self, *args, **kwargs) -> None:
            captured.args = args
            captured.kwargs = kwargs

    monkeypatch.setattr(temporary_memory, "TemporaryMemoryStore", FakeStore)
    hass = SimpleNamespace()

    result = temporary_memory._temporary_memory_store(hass, "entry", "agent")

    assert isinstance(result, FakeStore)
    assert captured.args == (
        hass,
        temporary_memory.STORAGE_VERSION,
        f"{temporary_memory.STORAGE_KEY_PREFIX}.entry.agent",
    )
    assert captured.kwargs == {
        "private": True,
        "atomic_writes": True,
        "serialize_in_event_loop": False,
    }


async def test_cold_snapshot_skips_corruption_without_creating_manager(
    monkeypatch,
) -> None:
    store = ValidationStorage(
        {
            "records": [
                None,
                _record("bad-expiry", expires_at="invalid"),
                _record("other", owner_scope_id="user:bob"),
                _record("visible"),
            ]
        }
    )
    monkeypatch.setattr(
        temporary_memory,
        "_temporary_memory_store",
        lambda _hass, _entry, _agent: store,
    )
    hass = SimpleNamespace(data={})

    result = await temporary_memory.async_read_temporary_memory_snapshot(
        hass,
        "entry",
        "agent",
        "conversation:kitchen",
        owner_scope_id="user:alice",
    )

    assert [record.memory_id for record in result] == ["visible"]
    assert temporary_memory.get_loaded_temporary_memory(hass, "entry", "agent") is None
    assert store.save_count == 0


async def test_get_temporary_memory_caches_and_initializes_manager(monkeypatch) -> None:
    created = []

    class FakeManager:
        def __init__(self, store) -> None:
            self.store = store
            self.async_initialize = AsyncMock()
            created.append(self)

    store = object()
    monkeypatch.setattr(temporary_memory, "TemporaryMemory", FakeManager)
    monkeypatch.setattr(
        temporary_memory,
        "_temporary_memory_store",
        lambda _hass, _entry, _agent: store,
    )
    hass = SimpleNamespace(data={})

    first = await temporary_memory.async_get_temporary_memory(hass, "entry", "agent")
    second = await temporary_memory.async_get_temporary_memory(hass, "entry", "agent")

    assert first is second is created[0]
    assert len(created) == 1
    assert first.store is store
    assert first.async_initialize.await_count == 2


async def test_temporary_memory_rejects_oversized_delete_without_partial_success() -> None:
    """The model contract and runtime reject more than 50 delete IDs."""
    storage = Storage()
    storage.async_save = AsyncMock(wraps=storage.async_save)
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    ids = [f"memory-{index}" for index in range(MAX_DELETE_RECORDS + 1)]

    with pytest.raises(ValueError, match="1 to 50"):
        await memory.async_delete(
            "scope", ids, owner_scope_id="user:test-owner"
        )

    delete_tool = next(
        tool
        for tool in temporary_memory_tools()
        if tool["spec"]["name"] == "temporary_memory_delete"
    )
    ids_schema = delete_tool["spec"]["parameters"]["properties"]["memory_ids"]
    assert ids_schema["minItems"] == 1
    assert ids_schema["maxItems"] == MAX_DELETE_RECORDS
    storage.async_save.assert_not_awaited()


async def test_unretained_request_has_no_active_temporary_memory_scope(monkeypatch) -> None:
    """Temporary retrieval is inert when the request lifecycle supplies no scope."""
    scope = unretained_scope(device_id="voice-device")
    assert scope.allows_retention is False

    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(data={"temporary_memory": "conversation"})
    active = AsyncMock(return_value=[])
    entity._temporary_memory = SimpleNamespace(async_active=active)
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda self: SimpleNamespace(temporary_memory=True),
    )
    token = _ACTIVE_TEMPORARY_SCOPE.set(None)
    try:
        assert await entity._async_retrieve_temporary_memories() == []
    finally:
        _ACTIVE_TEMPORARY_SCOPE.reset(token)

    active.assert_not_awaited()
