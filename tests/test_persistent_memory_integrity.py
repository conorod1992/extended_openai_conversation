"""Persistent-memory identity, mutation, and hybrid-retrieval integrity tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from custom_components.extended_openai_conversation_responses import memory as memory_module
from custom_components.extended_openai_conversation_responses.memory import PersistentMemory


class FakeStorage:
    """Detached in-memory storage."""

    def __init__(self, data=None) -> None:
        self.data = deepcopy(data)

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data) -> None:
        self.data = deepcopy(data)


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


async def test_new_keyed_upsert_does_not_merge_with_another_key() -> None:
    memory = await _memory()
    first = await memory.async_upsert(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    second = await memory.async_upsert(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.description",
    )

    assert first["status"] == second["status"] == "created"
    assert first["memory"]["memory_id"] != second["memory"]["memory_id"]

    updated = await memory.async_upsert(
        "alice",
        "Oscar is a Cavapoo.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    assert updated["status"] == "updated"
    assert updated["memory"]["memory_id"] == first["memory"]["memory_id"]
    other = next(
        item
        for item in await memory.async_list("alice")
        if item.key == "pet.oscar.description"
    )
    assert other.content == "Oscar is a Cavachon."


async def test_reconfirmation_updates_confirmation_not_fact_timestamp(
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

    created = await memory.async_upsert(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    confirmed = await memory.async_upsert(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    changed = await memory.async_upsert(
        "alice", "Oscar is a Cavapoo.", "pets", "explicit", key="pet.oscar.breed"
    )

    assert confirmed["memory"]["updated_at"] == created["memory"]["updated_at"]
    assert confirmed["memory"]["last_confirmed_at"] > created["memory"]["last_confirmed_at"]
    assert changed["memory"]["updated_at"] > confirmed["memory"]["updated_at"]


async def test_metadata_change_during_unkeyed_confirmation_updates_fact_timestamp(
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
    confirmed = await memory.async_upsert(
        "alice", "Tea is in the pantry.", "preferences", "explicit"
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["memory"]["updated_at"] > created["memory"]["updated_at"]


async def test_key_collision_does_not_mutate_memory_or_indexes() -> None:
    memory = await _memory()
    first = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    await memory.async_add(
        "alice", "Milo is a Labrador.", "pets", "explicit", key="pet.milo.breed"
    )
    before_memories = dict(memory._memories)
    before_tokens = {key: set(value) for key, value in memory._token_index.items()}
    before_keys = dict(memory._key_index)

    with pytest.raises(ValueError, match="canonical key already exists"):
        await memory.async_update(
            "alice", first["memory"]["memory_id"], key="pet.milo.breed"
        )

    assert memory._memories == before_memories
    assert {key: set(value) for key, value in memory._token_index.items()} == before_tokens
    assert memory._key_index == before_keys
    results = await memory.async_search("alice", "Oscar Cavachon")
    assert results and results[0].memory_id == first["memory"]["memory_id"]


async def test_keyed_upsert_embedding_invalidation_tracks_embedded_fields_only() -> None:
    memory = await _memory()
    await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    calls.clear()

    await memory.async_upsert(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    assert await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [["breed"]]
    calls.clear()

    await memory.async_upsert(
        "alice", "Oscar is a Cavapoo.", "pets", "explicit", key="pet.oscar.breed"
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
