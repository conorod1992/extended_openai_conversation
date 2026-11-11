"""Persistent-memory identity, mutation, and hybrid-retrieval integrity tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from custom_components.extended_openai_conversation_responses import (
    memory as memory_module,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)


class FakeStorage:
    """Detached in-memory storage."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)
        self.save_count = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        self.data = deepcopy(data)
        self.save_count += 1


async def _memory(data=None, cache=None) -> PersistentMemory:
    memory = PersistentMemory(FakeStorage(data), cache or FakeStorage())
    await memory.async_initialize()
    return memory


@pytest.mark.parametrize(
    "key",
    [
        "Pet Oscar Breed",
        "pet.óscar.breed",
        "pet..oscar",
        ".pet.oscar",
        "pet.oscar.",
        " pet.oscar.breed",
        "pet.oscar.breed ",
        "pet/oscar/breed",
    ],
)
async def test_noncanonical_keys_are_rejected_instead_of_rewritten(key: str) -> None:
    memory = await _memory()
    with pytest.raises(ValueError, match="lowercase dot-separated identifier"):
        await memory.async_add(
            "alice", "Oscar is a Cavachon.", "pets", "explicit", key=key
        )


async def test_key_validation_is_identical_for_backup_and_runtime() -> None:
    memory = await _memory()
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    assert created["memory"]["key"] == "pet.oscar.breed"

    backup = await memory.async_backup_data()
    backup["memories"][0]["key"] = "Pet Oscar Breed"
    with pytest.raises(ValueError, match="lowercase dot-separated identifier"):
        PersistentMemory.validate_backup_data(backup)


async def test_distinct_keys_preserve_distinct_facts_even_with_same_content() -> None:
    memory = await _memory()
    first = await memory.async_add(
        "alice", "The preferred temperature is 20 C.", "heating", "explicit", key="home.day_temperature"
    )
    second = await memory.async_add(
        "alice", "The preferred temperature is 20 C.", "heating", "explicit", key="home.night_temperature"
    )

    assert first["status"] == second["status"] == "created"
    assert first["memory"]["memory_id"] != second["memory"]["memory_id"]
    assert {item.key for item in await memory.async_list("alice")} == {
        "home.day_temperature",
        "home.night_temperature",
    }


@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_new_key_promotes_exact_unique_unkeyed_fact(operation: str) -> None:
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    first = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "implicit",
        subject="Oscar",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    assert first["status"] == "created"
    call = memory.async_add if operation == "add" else memory.async_propose
    promoted = await call(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="oscar",
        key="pet.oscar.breed",
    )
    if operation == "propose":
        assert promoted["status"] == "needs_resolution"
        assert promoted["candidate"]["memory_id"] == first["memory"]["memory_id"]
        assert promoted["candidate"]["key"] is None
        assert promoted["candidate"]["source"] == "implicit"
        [retained] = await memory.async_list("alice")
        assert retained.key is None and retained.source == "implicit"
        return
    assert promoted["status"] == "updated"
    assert promoted["memory"]["memory_id"] == first["memory"]["memory_id"]
    assert promoted["memory"]["created_at"] == first["memory"]["created_at"]
    assert promoted["memory"]["source"] == "explicit"
    assert promoted["memory"]["content"] == "Oscar is a Cavachon."
    assert promoted["memory"]["key"] == "pet.oscar.breed"
    assert promoted["memory"]["valid_from"] == "2026-01-01T00:00:00+00:00"
    assert len(await memory.async_list("alice")) == 1

    reloaded = PersistentMemory(storage)
    await reloaded.async_initialize()
    [record] = await reloaded.async_list("alice")
    assert record.memory_id == first["memory"]["memory_id"]
    assert record.key == "pet.oscar.breed"
    assert reloaded._key_index[("alice", "pet.oscar.breed")] == record.memory_id


@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_new_key_never_rekeys_differently_keyed_fact(operation: str) -> None:
    memory = await _memory()
    existing = await memory.async_propose(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    call = memory.async_add if operation == "add" else memory.async_propose
    second = await call(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.description",
    )
    if operation == "propose":
        assert second["status"] == "needs_resolution"
        assert second["candidate"]["memory_id"] == existing["memory"]["memory_id"]
        assert [r.key for r in await memory.async_list("alice")] == ["pet.oscar.breed"]
        return
    assert second["status"] == "created"
    assert second["memory"]["memory_id"] != existing["memory"]["memory_id"]
    assert {r.key for r in await memory.async_list("alice")} == {
        "pet.oscar.breed",
        "pet.oscar.description",
    }


@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_ambiguous_unkeyed_duplicates_are_not_promoted(operation: str) -> None:
    memory = await _memory()
    first = await memory.async_add(
        "alice", "The light is on.", "devices", "explicit", subject="Kitchen"
    )
    second = await memory.async_add(
        "alice", "The light is on.", "devices", "explicit", subject="Hall"
    )
    assert first["memory"]["memory_id"] != second["memory"]["memory_id"]
    call = memory.async_add if operation == "add" else memory.async_propose
    result = await call(
        "alice",
        "The light is on.",
        "devices",
        "explicit",
        subject="Kitchen",
        key="home.kitchen.light",
    )
    if operation == "propose":
        assert result["status"] == "needs_resolution"
        assert result["candidate"]["memory_id"] == first["memory"]["memory_id"]
        retained = await memory.async_list("alice")
        assert len(retained) == 2 and all(r.key is None for r in retained)
        return
    assert result["status"] == "created"
    original_ids = {
        first["memory"]["memory_id"],
        second["memory"]["memory_id"],
    }
    original_records = [
        record
        for record in await memory.async_list("alice")
        if record.memory_id in original_ids
    ]
    assert len(original_records) == 2
    assert all(record.key is None for record in original_records)
    assert len(await memory.async_list("alice")) == 3


@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_new_key_respects_subject_and_temporal_identity(operation: str) -> None:
    memory = await _memory()
    original = await memory.async_add(
        "alice",
        "The light is on.",
        "devices",
        "explicit",
        subject="Kitchen",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    call = memory.async_add if operation == "add" else memory.async_propose
    different_subject = await call(
        "alice",
        "The light is on.",
        "devices",
        "explicit",
        subject="Hall",
        key="home.hall.light",
    )
    different_time = await call(
        "alice",
        "The light is on.",
        "devices",
        "explicit",
        subject="Kitchen",
        key="home.kitchen.light",
        valid_from="2026-02-01T00:00:00+00:00",
    )
    assert different_subject["status"] == different_time["status"] == "created"
    assert original["memory"]["memory_id"] not in {
        different_subject["memory"]["memory_id"],
        different_time["memory"]["memory_id"],
    }
    restored = next(
        record for record in await memory.async_list("alice")
        if record.memory_id == original["memory"]["memory_id"]
    )
    assert restored.key is None
    assert restored.subject == "Kitchen"
    assert restored.valid_from == "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_implicit_new_key_cannot_relabel_explicit_memory(operation: str) -> None:
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    original = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", subject="Oscar"
    )
    before_saves = storage.save_count
    call = memory.async_add if operation == "add" else memory.async_propose
    result = await call(
        "alice",
        "Oscar is a Cavachon.",
        "other",
        "implicit",
        key="pet.oscar.breed",
    )
    assert result["status"] == ("duplicate" if operation == "add" else "needs_resolution")
    record = result.get("memory", result.get("candidate"))
    assert record["memory_id"] == original["memory"]["memory_id"]
    assert record["key"] is None
    assert record["source"] == "explicit"
    assert record["category"] == "pets"
    assert storage.save_count == before_saves
    assert len(await memory.async_list("alice")) == 1


@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_new_key_never_promotes_a_different_owner(operation: str) -> None:
    memory = await _memory()
    original = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit"
    )
    call = memory.async_add if operation == "add" else memory.async_propose
    result = await call(
        "bob", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    assert result["status"] == "created"
    assert result["memory"]["memory_id"] != original["memory"]["memory_id"]
    assert {r.key for r in await memory.async_list("alice")} == {None}
    assert {r.key for r in await memory.async_list("bob")} == {
        "pet.oscar.breed"
    }


@pytest.mark.parametrize("source", ["explicit", "implicit"])
@pytest.mark.parametrize("operation", ["add", "propose"])
async def test_card_like_identifiers_are_retained(source: str, operation: str) -> None:
    memory = await _memory()
    result = await getattr(memory, "async_" + operation)(
        "alice", "Package tracking reference 4111111111111111.", "deliveries", source,
    )
    assert result["status"] == "created"
    assert (await memory.async_list("alice"))[0].content == "Package tracking reference 4111111111111111."


async def test_non_card_identifier_remains_allowed() -> None:
    memory = await _memory()
    result = await memory.async_add(
        "alice", "Package tracking reference 4111111111111112.", "deliveries", "explicit"
    )
    assert result["status"] == "created"


async def test_key_promotion_preserves_an_existing_keyed_sibling() -> None:
    memory = await _memory()
    sibling = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    unkeyed = await memory.async_add(
        "alice", "Temporary note.", "pets", "explicit", subject="Oscar"
    )
    await memory.async_update(
        "alice", unkeyed["memory"]["memory_id"], content="Oscar is a Cavachon."
    )

    promoted = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.description",
    )
    assert promoted["status"] == "updated"
    assert promoted["memory"]["memory_id"] == unkeyed["memory"]["memory_id"]
    records = {record.memory_id: record for record in await memory.async_list("alice")}
    assert len(records) == 2
    assert records[sibling["memory"]["memory_id"]].key == "pet.oscar.breed"
    assert records[sibling["memory"]["memory_id"]].content == "Oscar is a Cavachon."
    assert records[unkeyed["memory"]["memory_id"]].key == "pet.oscar.description"


async def test_new_keyed_upsert_does_not_merge_with_another_key() -> None:
    memory = await _memory()
    first = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    second = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.description",
    )

    assert first["status"] == second["status"] == "created"
    assert first["memory"]["memory_id"] != second["memory"]["memory_id"]

    updated = await memory.async_update(
        "alice",
        first["memory"]["memory_id"],
        "Oscar is a Cavapoo.",
        "pets",
        source="explicit",
        key="pet.oscar.breed",
    )
    assert updated.memory_id == first["memory"]["memory_id"]
    other = next(
        item
        for item in await memory.async_list("alice")
        if item.key == "pet.oscar.description"
    )
    assert other.content == "Oscar is a Cavachon."


async def test_unchanged_upsert_preserves_fact_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moments = iter(
        [
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr(memory_module.dt_util, "utcnow", lambda: next(moments))
    memory = await _memory()

    created = await memory.async_propose(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    existing = await memory.async_propose(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    changed = await memory.async_update(
        "alice", created["memory"]["memory_id"], "Oscar is a Cavapoo.",
        expected_revision=existing["memory"]["revision"],
    )

    assert existing["memory"]["updated_at"] == created["memory"]["updated_at"]
    assert changed.updated_at > existing["memory"]["updated_at"]


async def test_metadata_change_during_unkeyed_upsert_updates_fact_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moments = iter(
        [datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 2, 2, tzinfo=UTC)]
    )
    monkeypatch.setattr(memory_module.dt_util, "utcnow", lambda: next(moments))
    memory = await _memory()
    created = await memory.async_add(
        "alice", "Tea is in the pantry.", "home", "explicit"
    )
    existing = await memory.async_update(
        "alice", created["memory"]["memory_id"], category="preferences",
    )

    assert existing.updated_at > created["memory"]["updated_at"]


async def test_key_collision_does_not_mutate_memory_or_indexes() -> None:
    memory = await _memory()
    first = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    await memory.async_add(
        "alice", "Milo is a Labrador.", "pets", "explicit", key="pet.milo.breed"
    )
    before_memories = dict(memory._memories)
    before_keys = dict(memory._key_index)

    with pytest.raises(ValueError, match="canonical key already exists"):
        await memory.async_update(
            "alice", first["memory"]["memory_id"], key="pet.milo.breed"
        )

    assert memory._memories == before_memories
    assert memory._key_index == before_keys
    results = await memory.async_search("alice", "Oscar Cavachon")
    assert results and results[0].memory_id == first["memory"]["memory_id"]


async def test_keyed_upsert_embedding_invalidation_tracks_embedded_fields_only() -> None:
    memory = await _memory()
    created = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    calls.clear()

    await memory.async_propose(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [["breed"]]
    calls.clear()

    await memory.async_update(
        "alice", created["memory"]["memory_id"], "Oscar is a Cavapoo.",
    )
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [
        ["pet.oscar.breed | pets | Oscar is a Cavapoo."],
        ["breed"],
    ]


async def test_hybrid_status_reports_configuration_fallback_and_recovery() -> None:
    memory = await _memory()
    assert memory.hybrid_status() == {
        "configured": False,
        "status": "lexical_fallback",
        "model": "default",
        "reason": "provider_not_configured",
    }

    async def failing(_inputs: list[str]) -> list[list[float]]:
        raise OSError("provider unavailable")

    memory.set_embedding_provider(failing, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "breed") is None
    assert memory.hybrid_status() == {
        "configured": True,
        "status": "lexical_fallback",
        "model": "test-model",
        "reason": "provider_error",
        "error_type": "OSError",
    }

    async def working(inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(working, "second-model")
    assert memory.hybrid_status() == {
        "configured": True,
        "status": "ready",
        "model": "second-model",
    }
    assert await memory.async_prepare_hybrid(["alice"], "breed") == [1.0, 0.0]
    assert memory.hybrid_status() == {
        "configured": True,
        "status": "active",
        "model": "second-model",
    }


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

    unchanged = await memory.async_update("alice", memory_id)
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


async def test_memory_startup_preserves_bad_records_without_exposing_them() -> None:
    """Usable facts remain readable; lossy saves and backups require repair."""
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

    memory = PersistentMemory(storage)
    await memory.async_initialize()

    assert [item.memory_id for item in await memory.async_list("alice")] == ["valid"]
    assert storage.save_count == 0
    assert storage.data == {"memories": [valid, malformed, duplicate_key]}
    with pytest.raises(ValueError, match="needs repair"):
        await memory.async_backup_data()

    reloaded = PersistentMemory(storage)
    await reloaded.async_initialize()
    assert [item.memory_id for item in await reloaded.async_list("alice")] == ["valid"]
    assert storage.save_count == 0
