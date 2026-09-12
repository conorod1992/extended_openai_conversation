"""Residual edge-case coverage for persistent conversation memory."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    memory as memory_module,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantEmbeddingCacheStorage,
    HomeAssistantMemoryStorage,
    MemoryRecord,
    MemoryStore,
    PersistentMemory,
)


class FakeStorage:
    """Detached in-memory persistence boundary."""

    def __init__(self, data: Any = None, *, fail_save: bool = False) -> None:
        self.data = deepcopy(data)
        self.fail_save = fail_save
        self.save_count = 0

    async def async_load(self) -> Any:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        if self.fail_save:
            raise OSError("storage unavailable")
        self.data = deepcopy(data)
        self.save_count += 1


def _record(
    memory_id: str = "one",
    *,
    user_id: str = "alice",
    key: str | None = None,
    content: str = "Oscar is a Cavachon.",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=user_id,
        content=content,
        category="pets",
        source="explicit",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        key=key,
        last_confirmed_at="2026-01-01T00:00:00+00:00",
    )


async def _memory(
    data: Any = None, cache: FakeStorage | None = None
) -> PersistentMemory:
    manager = PersistentMemory(FakeStorage(data), cache)
    await manager.async_initialize()
    return manager


async def test_store_migration_accepts_legacy_mapping_and_version_one() -> None:
    """Both supported legacy container shapes migrate and discard bad entries."""
    store = MemoryStore.__new__(MemoryStore)
    legacy = {"memories": [{"memory_id": "one"}, "bad"]}

    assert (await store._async_migrate_func(0, 0, legacy))["memories"][0][
        "importance"
    ] == "normal"
    assert (await store._async_migrate_func(1, 0, legacy))["memories"][0][
        "memory_id"
    ] == "one"
    with pytest.raises(NotImplementedError):
        await store._async_migrate_func(0, 0, "bad")


async def test_home_assistant_storage_adapters_delegate_load_and_save() -> None:
    """The two HA adapters transparently delegate their persistence operations."""
    for adapter_type in (
        HomeAssistantMemoryStorage,
        HomeAssistantEmbeddingCacheStorage,
    ):
        adapter = adapter_type.__new__(adapter_type)
        adapter._store = SimpleNamespace(
            async_load=AsyncMock(return_value={"value": 1}), async_save=AsyncMock()
        )
        assert await adapter.async_load() == {"value": 1}
        await adapter.async_save({"value": 2})
        adapter._store.async_save.assert_awaited_once_with({"value": 2})


def test_home_assistant_embedding_cache_uses_an_isolated_key(hass) -> None:
    durable = HomeAssistantMemoryStorage(hass, "entry", "agent")
    cache = HomeAssistantEmbeddingCacheStorage(hass, "entry", "agent")

    assert cache._store.key == f"{durable._store.key}.embeddings"


@pytest.mark.parametrize("data", [[], {}, {"memories": "bad"}])
async def test_initialization_self_heals_invalid_container_shapes(data: Any) -> None:
    storage = FakeStorage(data)
    manager = PersistentMemory(storage)

    await manager.async_initialize()
    await manager.async_initialize()

    assert storage.data == {"memories": []}
    assert storage.save_count == 1


async def test_initialization_truncates_at_capacity(monkeypatch) -> None:
    monkeypatch.setattr(memory_module, "MAX_MEMORIES_PER_AGENT", 1)
    storage = FakeStorage(
        {
            "memories": [
                memory_module._record_as_storage_dict(_record("one")),
                memory_module._record_as_storage_dict(_record("two")),
            ]
        }
    )

    manager = PersistentMemory(storage)
    await manager.async_initialize()

    assert list(manager._memories) == ["one"]
    assert len(storage.data["memories"]) == 1


async def test_uninitialized_manager_rejects_operations() -> None:
    manager = PersistentMemory(FakeStorage())

    with pytest.raises(RuntimeError, match="not been initialized"):
        manager.stats()


async def test_add_and_upsert_reject_invalid_source_and_capacity(monkeypatch) -> None:
    manager = await _memory()
    with pytest.raises(ValueError, match="source"):
        await manager.async_add("alice", "A durable fact.", "misc", "other")
    with pytest.raises(ValueError, match="source"):
        await manager.async_upsert("alice", "A durable fact.", "misc", "other")

    monkeypatch.setattr(memory_module, "MAX_MEMORIES_PER_AGENT", 0)
    with pytest.raises(ValueError, match="memory limit"):
        await manager.async_add("alice", "Another fact.", "misc", "explicit")
    with pytest.raises(ValueError, match="memory limit"):
        await manager.async_upsert("alice", "Another fact.", "misc", "explicit")


async def test_upsert_confirmation_applies_all_explicit_metadata() -> None:
    manager = await _memory()
    created = await manager.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )

    confirmed = await manager.async_upsert(
        "alice",
        "Oscar is a Cavachon",
        "animals",
        "explicit",
        importance="high",
        subject="Oscar",
        key=None,
        valid_from="2025-01-01T00:00:00+00:00",
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["memory"]["memory_id"] == created["memory"]["memory_id"]
    assert confirmed["memory"]["importance"] == "high"
    assert confirmed["memory"]["subject"] == "Oscar"
    assert confirmed["memory"]["key"] is None
    assert confirmed["memory"]["valid_from"] == "2025-01-01T00:00:00+00:00"


async def test_empty_search_and_noop_mutations_do_not_save() -> None:
    storage = FakeStorage()
    manager = PersistentMemory(storage)
    await manager.async_initialize()

    assert await manager.async_search("alice", "the and to") == []
    assert await manager.async_delete("alice", ["missing"]) == 0
    assert await manager.async_clear("alice") == 0
    assert await manager.async_reassign("alice", "bob", ["missing"]) == {
        "requested": 1,
        "reassigned": 0,
        "unchanged": 1,
    }
    assert storage.save_count == 0
    assert manager.stats()["memory_count"] == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"expected_revision": "bad"}, "expected_revision"),
        ({"clear_fields": "subject"}, "clear_fields"),
        ({"clear_fields": [1]}, "clear_fields"),
        ({"clear_fields": ["category"]}, "may contain"),
        ({"subject": "new", "clear_fields": ["subject"]}, "updated and cleared"),
    ],
)
async def test_update_rejects_malformed_concurrency_and_clear_inputs(
    kwargs: dict[str, Any], message: str
) -> None:
    manager = await _memory()
    with pytest.raises(ValueError, match=message):
        await manager.async_update("alice", "missing", **kwargs)


async def test_missing_and_wrong_owner_updates_are_indistinguishable() -> None:
    manager = await _memory()
    created = await manager.async_add("alice", "A durable fact.", "misc", "explicit")
    for memory_id in ("missing", created["memory"]["memory_id"]):
        with pytest.raises(ValueError, match="memory not found"):
            await manager.async_update("bob", memory_id, content="Changed.")


@pytest.mark.parametrize(
    "args",
    [("", "bob", ["one"]), ("alice", "alice", ["one"]), ("alice", "bob", [])],
)
async def test_reassign_validates_scope_and_selection(args: tuple[Any, ...]) -> None:
    manager = await _memory()
    with pytest.raises(ValueError):
        await manager.async_reassign(*args)


async def test_reassign_skips_target_key_collision() -> None:
    manager = await _memory()
    first = await manager.async_add(
        "alice", "Alice's dog is Oscar.", "pets", "explicit", key="pet.name"
    )
    await manager.async_add(
        "bob", "Bob's dog is Fido.", "pets", "explicit", key="pet.name"
    )

    result = await manager.async_reassign(
        "alice", "bob", [first["memory"]["memory_id"]]
    )

    assert result == {"requested": 1, "reassigned": 0, "unchanged": 1}


def test_backup_validation_rejects_container_count_ids_and_keys(monkeypatch) -> None:
    raw = memory_module._record_as_storage_dict(_record())
    with pytest.raises(ValueError, match="incomplete"):
        PersistentMemory.validate_backup_data({"memories": [], "extra": True})
    with pytest.raises(ValueError, match="count"):
        PersistentMemory.validate_backup_data({"memories": "bad"})
    with pytest.raises(ValueError, match="metadata"):
        PersistentMemory.validate_backup_data({"memories": [raw, raw]})

    keyed = memory_module._record_as_storage_dict(_record(key="pet.name"))
    second = memory_module._record_as_storage_dict(_record("two", key="pet.name"))
    with pytest.raises(ValueError, match="duplicate canonical key"):
        PersistentMemory.validate_backup_data({"memories": [keyed, second]})

    monkeypatch.setattr(memory_module, "MAX_MEMORIES_PER_AGENT", 0)
    with pytest.raises(ValueError, match="count"):
        PersistentMemory.validate_backup_data({"memories": [raw]})


async def test_replace_backup_revalidates_duplicate_ids_and_keys() -> None:
    manager = await _memory()
    with pytest.raises(ValueError, match="metadata"):
        await manager.async_replace_backup([_record(), _record()])
    with pytest.raises(ValueError, match="duplicate canonical key"):
        await manager.async_replace_backup(
            [_record(key="pet.name"), _record("two", key="pet.name")]
        )


def test_duplicate_related_and_index_edge_paths() -> None:
    manager = PersistentMemory(FakeStorage())
    first = _record(key="pet.oscar.breed")
    second = _record("two", key="pet.oscar.age", content="Oscar is five years old.")
    manager._memories = {first.memory_id: first, second.memory_id: second}
    manager._index(first)
    manager._index(second)

    assert manager._find_duplicate("alice", "Cavachon Oscar") == first
    assert (
        manager._find_related_candidate(
            "alice", "Oscar is a Labrador.", "Oscar", "pet.oscar.color"
        )
        is not None
    )
    assert (
        manager._find_related_candidate(
            "alice", "Oscar is a Labrador.", None, "pet.oscar.color"
        )
        is not None
    )
    assert manager._find_related_candidate("bob", "Oscar", None, None) is None

    manager._token_index.clear()
    manager._key_index[(first.user_id, first.key)] = "different"
    manager._unindex(first)
    assert manager._key_index[(first.user_id, first.key)] == "different"
    manager._key_index.clear()
    assert manager._replace_record(first) is first


async def test_hybrid_provider_change_and_wrong_vector_count_fall_back(
    monkeypatch,
) -> None:
    manager = await _memory()
    await manager.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")

    calls = 0

    async def wrong_count(_: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[1.0]] if calls == 1 else []

    manager.set_embedding_provider(wrong_count)
    assert await manager.async_prepare_hybrid(["alice"], "breed") is None
    assert manager.hybrid_status()["error_type"] == "ValueError"

    manager._embedding_cache.clear()

    async def no_vectors(_: list[str]) -> list[list[float]]:
        return []

    manager.set_embedding_provider(no_vectors)
    with pytest.raises(ValueError, match="wrong number"):
        await manager._async_refresh_missing_embeddings(["alice"])

    async def change_provider(_: list[str]) -> list[list[float]]:
        manager.set_embedding_provider(None)
        return [[1.0]]

    manager.set_embedding_provider(change_provider)
    assert await manager.async_prepare_hybrid(["alice"], "breed") is None

    async def provider_disappears(_: list[str]) -> bool:
        manager.set_embedding_provider(None)
        return True

    manager.set_embedding_provider(no_vectors)
    monkeypatch.setattr(
        manager, "_async_refresh_missing_embeddings", provider_disappears
    )
    assert await manager.async_prepare_hybrid(["alice"], "breed") is None
    assert manager.hybrid_status()["reason"] == "provider_changed"


@pytest.mark.parametrize(
    "data",
    [
        {"embeddings": []},
        {"embeddings": {1: {}}},
        {"embeddings": {"one": {"model": 1, "fingerprint": "x", "vector": [1]}}},
        {"embeddings": {"one": {"model": "m", "fingerprint": "x", "vector": []}}},
    ],
)
async def test_malformed_embedding_cache_is_discarded(data: Any) -> None:
    cache = FakeStorage(data)
    manager = PersistentMemory(FakeStorage(), cache)
    await manager.async_initialize()
    assert manager._embedding_cache == {}


async def test_embedding_cache_noop_and_storageless_save_paths() -> None:
    manager = await _memory()
    assert await manager._async_refresh_missing_embeddings(["alice"]) is False
    assert await manager._async_save_embedding_cache_locked() is True

    manager._embedding_cache_dirty = True
    assert await manager._async_save_embedding_cache_locked() is True
    assert manager._embedding_cache_dirty is False

    uninitialized = PersistentMemory(FakeStorage())
    uninitialized._embedding_cache_dirty = True
    assert await uninitialized._async_save_embedding_cache_locked() is True

    uninitialized_with_cache = PersistentMemory(FakeStorage(), FakeStorage())
    uninitialized_with_cache._embedding_cache_dirty = True
    assert await uninitialized_with_cache._async_save_embedding_cache_locked() is True


async def test_first_save_failure_has_no_committed_snapshot() -> None:
    manager = PersistentMemory(FakeStorage(fail_save=True))
    with pytest.raises(OSError, match="storage unavailable"):
        await manager._async_save_locked()


async def test_embedding_refresh_ignores_stale_record(monkeypatch) -> None:
    manager = await _memory()
    created = await manager.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit"
    )
    memory_id = created["memory"]["memory_id"]

    async def remove_while_embedding(_: list[str]) -> list[list[float]]:
        manager._memories.pop(memory_id)
        return [[1.0]]

    manager.set_embedding_provider(remove_while_embedding)
    assert await manager._async_refresh_missing_embeddings(["alice"]) is True
    assert manager._embedding_cache == {}


async def test_async_get_memory_caches_and_initializes_manager(monkeypatch) -> None:
    initialized = AsyncMock()
    manager = SimpleNamespace(async_initialize=initialized)
    constructor = Mock(return_value=manager)
    monkeypatch.setattr(memory_module, "PersistentMemory", constructor)
    monkeypatch.setattr(
        memory_module, "HomeAssistantMemoryStorage", lambda *args: "durable"
    )
    monkeypatch.setattr(
        memory_module, "HomeAssistantEmbeddingCacheStorage", lambda *args: "cache"
    )
    hass = SimpleNamespace(data={})

    assert await memory_module.async_get_memory(hass, "entry", "agent") is manager
    assert await memory_module.async_get_memory(hass, "entry", "agent") is manager
    constructor.assert_called_once_with("durable", "cache")
    assert initialized.await_count == 2


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("parties", "party"),
        ("running", "run"),
        ("matches", "match"),
    ],
)
def test_stemming_residual_suffixes(token: str, expected: str) -> None:
    assert memory_module._stem(token) == expected


def test_retrieval_math_and_embedding_validation_edges() -> None:
    assert memory_module._fuzzy_relevance({"cavchon"}, {"cavachon"}) > 0
    assert memory_module._cosine_similarity(None, [1.0]) is None
    assert memory_module._cosine_similarity([0.0], [1.0]) is None
    with pytest.raises(ValueError, match="numeric sequence"):
        memory_module._clean_embedding("1,2")
    with pytest.raises(ValueError, match="invalid"):
        memory_module._clean_embedding([math.inf])


@pytest.mark.parametrize(
    "raw",
    [
        "not an object",
        {"memory_id": "one"},
        memory_module._record_as_storage_dict(replace(_record(), source="other")),
        memory_module._record_as_storage_dict(
            replace(_record(), created_at="not-a-time")
        ),
        memory_module._record_as_storage_dict(
            replace(_record(), valid_from="not-a-time")
        ),
    ],
)
def test_persistent_record_validation_rejects_malformed_records(raw: Any) -> None:
    with pytest.raises(ValueError):
        memory_module._validate_persistent_memory_record(raw)


def test_input_cleaners_reject_wrong_types_lengths_and_timestamps() -> None:
    for function, value in (
        (memory_module._clean_content, 1),
        (memory_module._clean_category, 1),
        (memory_module._clean_importance, "urgent"),
    ):
        with pytest.raises(ValueError):
            function(value)
    assert memory_module._clean_optional("   ", "subject", 3) is None
    with pytest.raises(ValueError):
        memory_module._clean_optional(1, "subject", 3)
    with pytest.raises(ValueError):
        memory_module._clean_optional("long", "subject", 3)
    with pytest.raises(ValueError):
        memory_module._clean_category("")
    with pytest.raises(ValueError):
        memory_module._clean_key(1)
    with pytest.raises(ValueError):
        memory_module._clean_key("x" * (memory_module.MAX_KEY_LENGTH + 1))
    with pytest.raises(ValueError, match="ISO 8601"):
        memory_module._clean_timestamp("yesterday", "valid_from")


def test_checksum_rejects_malformed_candidates() -> None:
    assert memory_module._passes_luhn_checksum("123") is False
    assert memory_module._is_valid_iban("not-an-iban") is False


def test_memory_tool_metadata_schema_has_scope_only_when_requested() -> None:
    assert "scope" not in memory_module._memory_metadata_schema(include_scope=False)
    assert "scope" in memory_module._memory_metadata_schema(include_scope=True)
