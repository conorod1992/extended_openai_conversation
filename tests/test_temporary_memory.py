"""Temporary-memory lifecycle, ownership, and safety tests."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_ACTIVE_RECORDS,
    MAX_INJECT_RECORDS,
    TemporaryMemory,
)
from custom_components.extended_openai_conversation_responses.temporary_memory_performance import (
    install_temporary_memory_read_fast_path,
)
from homeassistant.util import dt as dt_util

# Exercise the same effective method composition installed by integration startup.
install_temporary_memory_read_fast_path()


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
    assert (
        await memory.async_active("device:kitchen", owner_scope_id="user:bob") == []
    )

    restored = TemporaryMemory(Storage(storage.data))
    await restored.async_initialize()
    assert len(
        await restored.async_active(
            "conversation:fresh", owner_scope_id="user:alice"
        )
    ) == 1
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
    assert (
        await memory.async_active("user:alice", owner_scope_id="user:alice") == []
    )
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
