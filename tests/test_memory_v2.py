"""Persistent-memory v2 retrieval, metadata, scope, and bundle tests."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _ACTIVE_MEMORY_SESSION,
    _ACTIVE_SCOPE,
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.scope import (
    SHARED_HOUSEHOLD_SCOPE_ID,
    shared_scope,
    unretained_scope,
    user_scope,
)


class FakeStorage:
    """Detached in-memory storage."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


async def _memory(data=None) -> PersistentMemory:
    memory = PersistentMemory(FakeStorage(data))
    await memory.async_initialize()
    return memory


async def test_metadata_defaults_freshness_and_backup_round_trip() -> None:
    memory = await _memory()
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="Pet Oscar Breed",
        valid_from="2025-01-01T00:00:00+00:00",
    )
    record = (await memory.async_list("alice"))[0]
    assert record.importance == "normal"
    assert record.key == "pet.oscar.breed"
    assert record.last_confirmed_at == record.created_at
    assert record.valid_from == "2025-01-01T00:00:00+00:00"
    backup = await memory.async_backup_data()
    assert "embedding" not in backup["memories"][0]
    restored = PersistentMemory.validate_backup_data(backup)
    assert restored[0].memory_id == created["memory"]["memory_id"]
    assert restored[0].subject == "Oscar"


async def test_bm25_phrase_stemming_typo_metadata_and_importance() -> None:
    memory = await _memory()
    rare = await memory.async_add(
        "alice",
        "Oscar is a Cavachon family dog.",
        "pets",
        "explicit",
        "normal",
        "Oscar",
        "pet.oscar.breed",
    )
    await memory.async_add(
        "alice", "The dogs enjoy walking in parks.", "pets", "explicit"
    )
    phrase = await memory.async_search("alice", "Oscar Cavachon")
    assert phrase[0].memory_id == rare["memory"]["memory_id"]
    assert await memory.async_search("alice", "dog walk")
    assert await memory.async_search("alice", "Cavchon breed")

    normal = await memory.async_add(
        "alice", "Office heating target is 19 Celsius.", "heating", "explicit"
    )
    high = await memory.async_add(
        "alice",
        "Bedroom heating target is 19 Celsius.",
        "heating",
        "explicit",
        "high",
    )
    unrelated = await memory.async_add(
        "alice", "Passport is in the blue drawer.", "travel", "explicit", "high"
    )
    ranked = await memory.async_search("alice", "heating target 19 Celsius")
    assert ranked[0].memory_id == high["memory"]["memory_id"]
    assert normal["memory"]["memory_id"] in {item.memory_id for item in ranked}
    assert unrelated["memory"]["memory_id"] not in {item.memory_id for item in ranked}


async def test_upsert_key_uniqueness_confirmation_conflict_and_scope() -> None:
    memory = await _memory()
    created = await memory.async_upsert(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    updated = await memory.async_upsert(
        "alice", "Oscar is a Cockapoo.", "pets", "explicit", key="pet.oscar.breed"
    )
    assert created["status"] == "created"
    assert updated["status"] == "updated"
    assert len(await memory.async_list("alice")) == 1
    household = await memory.async_upsert(
        SHARED_HOUSEHOLD_SCOPE_ID,
        "The household dog is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    assert household["status"] == "created"
    confirmed = await memory.async_upsert(
        "alice", "Oscar is a Cockapoo", "pets", "explicit"
    )
    assert confirmed["status"] == "confirmed"
    conflict = await memory.async_upsert(
        "alice", "Oscar is a Labrador.", "pets", "explicit", subject="Oscar"
    )
    assert conflict["status"] == "needs_resolution"

    with pytest.raises(ValueError, match="canonical key"):
        await memory.async_add(
            "alice", "Duplicate keyed fact.", "pets", "explicit", key="pet.oscar.breed"
        )

    movable = await memory.async_add(
        "alice", "Tea bags are in the pantry.", "home", "explicit"
    )
    moved = await memory.async_update(
        "alice",
        movable["memory"]["memory_id"],
        target_user_id=SHARED_HOUSEHOLD_SCOPE_ID,
    )
    assert moved.user_id == SHARED_HOUSEHOLD_SCOPE_ID
    assert movable["memory"]["memory_id"] not in {
        item.memory_id for item in await memory.async_list("alice")
    }


async def test_hybrid_semantic_and_lexical_fallback() -> None:
    memory = await _memory()
    created = await memory.async_add(
        "alice", "Keep the nursery warm.", "heating", "explicit"
    )
    assert await memory.async_prepare_hybrid(["alice"], "heating temperature") is None

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings)
    query_embedding = await memory.async_prepare_hybrid(
        ["alice"], "heating temperature"
    )
    found = await memory.async_search(
        ["alice"],
        "heating temperature",
        hybrid=True,
        query_embedding=query_embedding,
    )
    assert found[0].memory_id == created["memory"]["memory_id"]


async def test_conversation_bundle_selects_once_reuses_and_resets() -> None:
    first = MemoryRecord(
        "one", "alice", "Alice likes tea.", "preferences", "explicit", "a", "a"
    )
    current = {"one": first}

    async def get_many(references, scopes):
        return [
            current[memory_id] for scope, memory_id in references if scope in scopes
        ]

    memory = SimpleNamespace(
        async_search=AsyncMock(return_value=[first]),
        async_get_many=AsyncMock(side_effect=get_many),
    )
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(
        data={
            CONF_FUNCTION_TOOLS: "[]",
            CONF_MEMORY_ENABLED: True,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
        }
    )
    entity._memory = memory
    entity._continuity = ConversationContinuity("agent")
    context = SimpleNamespace(context=SimpleNamespace(user_id="alice"))
    scope_token = _ACTIVE_SCOPE.set(user_scope("alice", source="test"))
    session_token = _ACTIVE_MEMORY_SESSION.set(("conversation:first", 5))
    try:
        assert await entity._async_retrieve_memories(context, "tea") == [first]
        current["one"] = MemoryRecord(
            "one", "alice", "Alice likes coffee.", "preferences", "explicit", "a", "b"
        )
        second = await entity._async_retrieve_memories(context, "unrelated topic")
        assert second[0].content == "Alice likes coffee."
        assert memory.async_search.await_count == 1
        _ACTIVE_MEMORY_SESSION.reset(session_token)
        session_token = _ACTIVE_MEMORY_SESSION.set(("conversation:second", 5))
        await entity._async_retrieve_memories(context, "tea")
        assert memory.async_search.await_count == 2
    finally:
        _ACTIVE_MEMORY_SESSION.reset(session_token)
        _ACTIVE_SCOPE.reset(scope_token)


def test_scope_composition_and_strict_write_destinations() -> None:
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(
        data={CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT}
    )
    context = SimpleNamespace(context=SimpleNamespace(user_id="alice"))

    token = _ACTIVE_SCOPE.set(user_scope("alice", source="test"))
    try:
        assert entity._current_readable_memory_scope_ids(context) == [
            "alice",
            SHARED_HOUSEHOLD_SCOPE_ID,
        ]
        assert (
            entity._current_write_memory_scope_id(None, context, source="explicit")
            == "alice"
        )
        assert (
            entity._current_write_memory_scope_id(
                "household", context, source="explicit"
            )
            == SHARED_HOUSEHOLD_SCOPE_ID
        )
    finally:
        _ACTIVE_SCOPE.reset(token)

    token = _ACTIVE_SCOPE.set(shared_scope(source="test"))
    try:
        assert entity._current_readable_memory_scope_ids(context) == [
            SHARED_HOUSEHOLD_SCOPE_ID
        ]
        with pytest.raises(ValueError, match="cannot write personal"):
            entity._current_write_memory_scope_id(
                "personal", context, source="explicit"
            )
    finally:
        _ACTIVE_SCOPE.reset(token)

    token = _ACTIVE_SCOPE.set(unretained_scope())
    try:
        assert entity._current_readable_memory_scope_ids(context) == []
    finally:
        _ACTIVE_SCOPE.reset(token)
