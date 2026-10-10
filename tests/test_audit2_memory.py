"""Memory identity, multilingual retrieval and cache responsiveness regressions."""

import asyncio
from dataclasses import FrozenInstanceError
import threading

import pytest

from custom_components.extended_openai_conversation_responses import memory as module
from custom_components.extended_openai_conversation_responses.memory import (
    EmbeddingCacheEntry,
    PersistentMemory,
)
from tests.test_memory import FakeStorage


@pytest.mark.parametrize("operation", ["async_add", "async_upsert"])
@pytest.mark.parametrize(
    ("first_subject", "second_subject"),
    [("Oscar", "Luna"), ("C++", "C#"), ("Room 1.2", "Room 1/2")],
)
async def test_equal_content_for_distinct_subjects_remains_distinct(
    operation, first_subject, second_subject
):
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    add = getattr(memory, operation)
    first = await add(
        "user", "Likes chicken", "preferences", "explicit", subject=first_subject
    )
    second = await add(
        "user", "Likes chicken", "preferences", "explicit", subject=second_subject
    )
    repeated = await add(
        "user",
        "Likes chicken",
        "preferences",
        "explicit",
        subject=first_subject.upper(),
    )
    assert first["status"] == second["status"] == "created"
    assert repeated["memory"]["memory_id"] == first["memory"]["memory_id"]
    assert len(memory._memories) == 2


async def test_canonical_key_still_establishes_identity():
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    first = await memory.async_upsert(
        "user",
        "Likes chicken",
        "preferences",
        "explicit",
        subject="Oscar",
        key="pet.food",
    )
    second = await memory.async_upsert(
        "user", "Likes fish", "preferences", "explicit", subject="Luna", key="pet.food"
    )
    assert first["memory"]["memory_id"] == second["memory"]["memory_id"]
    assert second["status"] == "updated"


@pytest.mark.parametrize("operation", ["async_add", "async_upsert"])
@pytest.mark.parametrize("legacy_source", ["implicit", "explicit"])
async def test_subject_confirmation_prevents_legacy_fact_matching_other_subjects(
    operation, legacy_source
):
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    original = await memory.async_add(
        "user", "Likes chicken", "preferences", legacy_source
    )
    confirmed = await getattr(memory, operation)(
        "user", "Likes chicken", "preferences", "explicit", subject="Oscar"
    )
    assert confirmed["memory"]["memory_id"] == original["memory"]["memory_id"]
    assert confirmed["memory"]["subject"] == "Oscar"
    assert confirmed["memory"]["source"] == "explicit"

    reloaded = PersistentMemory(storage)
    await reloaded.async_initialize()
    other = await getattr(reloaded, operation)(
        "user", "Likes chicken", "preferences", "explicit", subject="Luna"
    )
    assert other["status"] == "created"
    assert {record.subject for record in await reloaded.async_list("user")} == {
        "Oscar",
        "Luna",
    }


async def test_implicit_add_does_not_assign_subject_to_explicit_legacy_fact():
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    await memory.async_add("user", "Likes chicken", "preferences", "explicit")
    saved = storage.save_count
    result = await memory.async_add(
        "user", "Likes chicken", "preferences", "implicit", subject="Oscar"
    )
    assert result["status"] == "duplicate"
    assert result["memory"]["subject"] is None
    assert result["memory"]["source"] == "explicit"
    assert storage.save_count == saved


@pytest.mark.parametrize(
    "query", ["What is my dog's name?", "What is my dog\u2019s name?"]
)
async def test_possessive_search_retrieves_dog_name(query):
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    await memory.async_add("user", "The dog is called Oscar", "pets", "explicit")
    results = await memory.async_search("user", query)
    assert len(results) == 1 and "Oscar" in results[0].content


@pytest.mark.parametrize(
    "content,query",
    [
        ("我的猫叫小白", "猫"),
        ("私は東京に住んでいます", "東京"),
        ("고양이이름은나비", "고양이"),
    ],
)
async def test_continuous_cjk_keyword_retrieval(content, query):
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    await memory.async_add("user", content, "facts", "explicit")
    assert len(await memory.async_search("user", query)) == 1


async def test_semantics_do_not_require_lexical_tokens():
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    result = await memory.async_add("user", "A cat fact", "pets", "explicit")
    record = memory._memories[result["memory"]["memory_id"]]
    memory._embedding_cache[record.memory_id] = EmbeddingCacheEntry(
        memory._embedding_model, module._embedding_fingerprint(record), [1.0, 0.0]
    )
    assert module._token_list("🐈") == []
    assert await memory.async_search(
        "user", "🐈", hybrid=True, query_embedding=[1.0, 0.0]
    ) == [record]
    assert await memory.async_search("user", "🐈") == []
    assert (
        await memory.async_search(
            "another-user", "🐈", hybrid=True, query_embedding=[1.0, 0.0]
        )
        == []
    )


async def test_large_cache_payload_runs_off_loop_with_stable_vectors(monkeypatch):
    cache = FakeStorage()
    memory = PersistentMemory(FakeStorage(), cache)
    await memory.async_initialize()
    result = await memory.async_add("user", "A cat fact", "pets", "explicit")
    record = memory._memories[result["memory"]["memory_id"]]
    vector = [0.1] * 3072
    entry = EmbeddingCacheEntry("model", "fingerprint", vector)
    vector[0] = 99
    assert entry.vector[0] == 0.1
    with pytest.raises(FrozenInstanceError):
        entry.vector = ()
    memory._embedding_cache = {str(index): entry for index in range(2000)}
    memory._embedding_cache[record.memory_id] = entry
    assert memory._snapshot_mutation_state().embedding_cache[record.memory_id] is entry
    memory._embedding_cache_dirty = True
    loop_thread = threading.get_ident()
    real = module._embedding_cache_payload
    entered, release = threading.Event(), threading.Event()

    def slow_payload(*args):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(5)
        return real(*args)

    monkeypatch.setattr(module, "_embedding_cache_payload", slow_payload)
    saving = asyncio.create_task(memory._async_save_embedding_cache_locked())
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        # Reaching this point while conversion is blocked proves loop progress.
        assert not saving.done()
    finally:
        release.set()
    assert await saving
    assert cache.data["embeddings"][record.memory_id]["vector"] == list(entry.vector)
