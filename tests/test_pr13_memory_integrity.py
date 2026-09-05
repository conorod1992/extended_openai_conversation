"""Focused regressions for PR13 memory integrity fixes."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_RETRIEVAL_MODE,
    MEMORY_RETRIEVAL_HYBRID,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_TEMPORARY_SCOPE,
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory
from custom_components.extended_openai_conversation_responses.scope import unretained_scope
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    MAX_DELETE_RECORDS,
    TemporaryMemory,
    temporary_memory_tools,
)


class FakeStorage:
    """Detached persistence double with save tracking."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = deepcopy(data)
        self.save_count = 0

    async def async_load(self) -> dict[str, Any] | None:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)
        self.save_count += 1


async def _memory(storage: FakeStorage | None = None) -> PersistentMemory:
    memory = PersistentMemory(storage or FakeStorage())
    await memory.async_initialize()
    return memory


async def test_temporary_memory_rejects_oversized_delete_without_partial_success() -> None:
    """The model contract and runtime reject more than 50 delete IDs."""
    storage = FakeStorage()
    memory = TemporaryMemory(storage)
    await memory.async_initialize()
    ids = [f"memory-{index}" for index in range(MAX_DELETE_RECORDS + 1)]

    with pytest.raises(ValueError, match="1 to 50"):
        await memory.async_delete("scope", ids)

    delete_tool = next(
        tool
        for tool in temporary_memory_tools()
        if tool["spec"]["name"] == "temporary_memory_delete"
    )
    ids_schema = delete_tool["spec"]["parameters"]["properties"]["memory_ids"]
    assert ids_schema["minItems"] == 1
    assert ids_schema["maxItems"] == MAX_DELETE_RECORDS
    assert storage.save_count == 0


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


async def test_memory_metadata_can_be_explicitly_cleared() -> None:
    """Omitted metadata remains unchanged while clear_fields can remove it."""
    memory = await _memory()
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    memory_id = created["memory"]["memory_id"]

    unchanged = await memory.async_update("alice", memory_id, importance="high")
    assert unchanged.subject == "Oscar"
    assert unchanged.key == "pet.oscar.breed"
    assert unchanged.valid_from == "2026-01-01T00:00:00+00:00"

    cleared = await memory.async_update(
        "alice",
        memory_id,
        clear_fields=["subject", "key", "valid_from"],
    )
    assert cleared.subject is None
    assert cleared.key is None
    assert cleared.valid_from is None

    replacement = await memory.async_add(
        "alice",
        "Oscar's breed record was replaced.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    assert replacement["status"] == "created"


async def test_memory_startup_canonical_validation_self_heals_bad_records() -> None:
    """Malformed and duplicate-key records are dropped and the clean set is saved."""
    timestamp = "2026-09-01T12:00:00+00:00"
    valid = {
        "memory_id": "valid",
        "user_id": "alice",
        "content": "Oscar is a Cavachon.",
        "category": "pets",
        "source": "explicit",
        "created_at": timestamp,
        "updated_at": timestamp,
        "importance": "normal",
        "subject": "Oscar",
        "key": "pet.oscar.breed",
        "valid_from": None,
        "last_confirmed_at": timestamp,
    }
    malformed = {
        **valid,
        "memory_id": "malformed",
        "content": "   ",
        "key": "pet.oscar.other",
    }
    duplicate_key = {
        **valid,
        "memory_id": "duplicate",
        "content": "A second record reuses the canonical key.",
    }
    storage = FakeStorage({"memories": [valid, malformed, duplicate_key]})

    memory = await _memory(storage)

    assert [item.memory_id for item in await memory.async_list("alice")] == ["valid"]
    assert storage.save_count == 1
    assert storage.data == {"memories": [valid]}

    reloaded = await _memory(storage)
    assert [item.memory_id for item in await reloaded.async_list("alice")] == ["valid"]
    assert storage.save_count == 1


async def test_explicit_memory_search_uses_configured_hybrid_semantics() -> None:
    """Model-facing search shares the same Hybrid preparation path as auto retrieval."""
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(
        data={CONF_MEMORY_RETRIEVAL_MODE: MEMORY_RETRIEVAL_HYBRID}
    )
    expected = [SimpleNamespace(memory_id="one")]
    entity._memory = SimpleNamespace(
        async_prepare_hybrid=AsyncMock(return_value=[1.0, 0.0]),
        async_search=AsyncMock(return_value=expected),
    )

    result = await entity._async_search_memories(
        ["alice"], "Oscar breed", 5, "pets"
    )

    assert result == expected
    entity._memory.async_prepare_hybrid.assert_awaited_once_with(
        ["alice"], "Oscar breed"
    )
    entity._memory.async_search.assert_awaited_once_with(
        ["alice"],
        "Oscar breed",
        "pets",
        5,
        query_embedding=[1.0, 0.0],
        hybrid=True,
    )
