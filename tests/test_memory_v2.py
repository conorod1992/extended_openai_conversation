"""Persistent-memory v2 retrieval, metadata, scope, and bundle tests."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_SHARED_MEMORY_MODE,
    MEMORY_RETRIEVAL_HYBRID,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
    _ACTIVE_TURN,
)
from custom_components.extended_openai_conversation_responses.memory import (
    EMBEDDING_CACHE_BATCH_SIZE,
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


class FailingCacheStorage(FakeStorage):
    """Embedding cache that cannot persist generated data."""

    async def async_save(self, data):
        raise OSError("cache unavailable")


async def _memory(data=None) -> PersistentMemory:
    memory = PersistentMemory(FakeStorage(data))
    await memory.async_initialize()
    return memory


@pytest.mark.parametrize(
    ("word", "other", "stem"),
    [
        ("calling", "call", "call"),
        ("falling", "fall", "fall"),
        ("filling", "fill", "fill"),
        ("rolling", "roll", "roll"),
        ("pressing", "press", "press"),
        ("dressing", "dress", "dress"),
        ("spelling", "spell", "spell"),
        ("breed", "breeds", "breed"),
        ("speed", "speeds", "speed"),
        ("running", "run", "run"),
        ("shopping", "shop", "shop"),
    ],
)
def test_memory_stemmer_preserves_real_word_endings(word, other, stem) -> None:
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert _stem(word) == _stem(other) == stem


@pytest.mark.parametrize(
    ("base", "inflected"),
    [
        ("like", "liked"), ("like", "liking"), ("love", "loved"),
        ("live", "lived"), ("move", "moved"), ("name", "named"),
        ("change", "changed"), ("change", "changing"),
        ("stop", "stopped"), ("plan", "planned"),
        ("glass", "glasses"), ("class", "classes"),
        ("movie", "movies"), ("cookie", "cookies"),
        ("brownie", "brownies"), ("size", "sizes"), ("shoe", "shoes"),
        ("pass", "passed"), ("dress", "dressed"), ("roll", "rolled"),
    ],
)
def test_memory_regains_common_past_and_plural_word_families(
    base: str, inflected: str
) -> None:
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert _stem(base) == _stem(inflected)


@pytest.mark.parametrize(
    ("stored", "query"),
    [
        ("I liked coffee.", "likes"),
        ("I loved coffee.", "love"),
        ("I have moved.", "move"),
        ("I changed my name.", "change"),
        ("My bicycle stopped.", "stop"),
        ("We planned a trip.", "plan"),
        ("My glasses broke.", "glass"),
        ("My favourite movies are documentaries.", "movie"),
        ("I bought some cookies.", "cookie"),
    ],
)
async def test_memory_real_search_recovers_past_and_plural_forms(
    stored: str, query: str
) -> None:
    memory = await _memory()
    added = await memory.async_add("alice", stored, "personal", "explicit")
    results = await memory.async_search("alice", query)
    assert any(row.memory_id == added["memory"]["memory_id"] for row in results)



@pytest.mark.parametrize(
    ("base", "inflections"),
    [
        ("control", ("controlled", "controlling", "controls")),
        ("cancel", ("cancelled", "canceled", "cancelling", "canceling")),
        ("travel", ("travelled", "traveled", "travelling", "traveling")),
        ("label", ("labelled", "labeled", "labelling", "labeling")),
        ("model", ("modelled", "modeled", "modelling", "modeling")),
        ("signal", ("signalled", "signaled", "signalling", "signaling")),
        ("patrol", ("patrolled", "patrolling")),
        ("compel", ("compelled", "compelling")),
        ("expel", ("expelled", "expelling")),
        ("propel", ("propelled", "propelling")),
        ("add", ("added", "adding")),
        ("egg", ("egged", "egging")),
        ("ebb", ("ebbed", "ebbing")),
        ("run", ("running",)),
        ("stop", ("stopped", "stopping", "stops")),
        ("call", ("called", "calling", "calls")),
        ("fill", ("filled", "filling", "fills")),
        ("install", ("installed", "installing", "installs")),
        ("sell", ("selling", "sells")),
    ],
)
def test_memory_doubled_consonant_families_retain_correct_roots(
    base: str, inflections: tuple[str, ...]
) -> None:
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert all(_stem(word) == _stem(base) for word in inflections)


@pytest.mark.parametrize(
    ("stored", "query"),
    [
        ("I controlled the thermostat.", "control"),
        ("I cancelled the gym.", "cancel"),
        ("I travelled yesterday.", "travel"),
        ("I added a reminder.", "add"),
        ("I egged the pudding.", "egg"),
    ],
)
async def test_memory_new_doubled_roots_are_retrievable(
    stored: str, query: str
) -> None:
    memory = await _memory()
    record = await memory.async_add("alice", stored, "personal", "explicit")
    matches = await memory.async_search("alice", query)
    assert [item.memory_id for item in matches] == [record["memory"]["memory_id"]]


@pytest.mark.parametrize(
    ("original", "revised"),
    [
        ("I cancelled gym.", "I cancel gym."),
        ("I added reminder.", "I add reminder."),
    ],
)
async def test_memory_related_roots_do_not_suppress_distinct_proposals(
    original: str, revised: str
) -> None:
    memory = await _memory()
    created = await memory.async_add("alice", original, "personal", "explicit")
    response = await memory.async_propose("alice", revised, "personal", "explicit")
    assert response["status"] == "created"
    assert response["memory"]["memory_id"] != created["memory"]["memory_id"]
    assert len(await memory.async_list("alice")) == 2


def test_memory_consonant_doubling_does_not_create_silent_e_word() -> None:
    """A doubled consonant distinguishes hopping from hoping."""
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert _stem("hopped") == _stem("hopping") == _stem("hop") == "hop"
    assert _stem("hoped") == _stem("hoping") == _stem("hope") == "hope"
    assert _stem("hop") != _stem("hope")


async def test_memory_lexical_search_keeps_hop_and_hope_separate() -> None:
    memory = await _memory()
    hopped = await memory.async_add(
        "alice", "I hopped.", "personal", "explicit"
    )
    hoped = await memory.async_add(
        "alice", "I hoped.", "personal", "explicit"
    )
    hop = await memory.async_search("alice", "hop")
    hope = await memory.async_search("alice", "hope")
    assert [row.memory_id for row in hop] == [hopped["memory"]["memory_id"]]
    assert [row.memory_id for row in hope] == [hoped["memory"]["memory_id"]]


async def test_memory_retrieval_stems_do_not_determine_proposal_identity() -> None:
    memory = await _memory()
    original = await memory.async_add(
        "alice", "I liked coffee.", "preferences", "explicit"
    )
    resolution = await memory.async_propose(
        "alice", "I like coffee.", "preferences", "explicit"
    )
    assert resolution["status"] == "created"
    assert resolution["memory"]["memory_id"] != original["memory"]["memory_id"]
    assert len(await memory.async_list("alice")) == 2




def test_memory_uses_exact_shared_stemmer_after_extraction() -> None:
    """The refactor must not change the Memory stemmer independently of Archive."""
    from custom_components.extended_openai_conversation_responses import memory
    from custom_components.extended_openai_conversation_responses.retrieval_text import (
        _stem as shared_stem,
    )

    assert memory._stem is shared_stem
    assert shared_stem("controlled") == "control"
    assert shared_stem("adding") == "add"
    assert shared_stem("hopped") == "hop"
    assert shared_stem("hoped") == "hope"


async def test_memory_search_recovers_call_and_breed_families() -> None:
    memory = await _memory()
    call = await memory.async_add(
        "alice", "Calling the plumber tomorrow", "reminders", "explicit"
    )
    breed = await memory.async_add(
        "alice", "Breed information is on the vet form", "pets", "explicit"
    )
    found_call = await memory.async_search("alice", "call")
    found_breed = await memory.async_search("alice", "breeds")
    assert any(row.memory_id == call["memory"]["memory_id"] for row in found_call)
    assert any(row.memory_id == breed["memory"]["memory_id"] for row in found_breed)


async def test_metadata_defaults_and_backup_round_trip() -> None:
    memory = await _memory()
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
        valid_from="2025-01-01T00:00:00+00:00",
    )
    record = (await memory.async_list("alice"))[0]
    assert record.key == "pet.oscar.breed"
    assert record.valid_from == "2025-01-01T00:00:00+00:00"
    backup = await memory.async_backup_data()
    assert "embedding" not in backup["memories"][0]
    restored = PersistentMemory.validate_backup_data(backup)
    assert restored[0].memory_id == created["memory"]["memory_id"]
    assert restored[0].subject == "Oscar"


async def test_bm25_phrase_stemming_typo_metadata() -> None:
    memory = await _memory()
    rare = await memory.async_add(
        "alice",
        "Oscar is a Cavachon family dog.",
        "pets",
        "explicit",
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
    )
    unrelated = await memory.async_add(
        "alice", "Passport is in the blue drawer.", "travel", "explicit"
    )
    ranked = await memory.async_search("alice", "heating target 19 Celsius")
    assert {item.memory_id for item in ranked} == {high["memory"]["memory_id"], normal["memory"]["memory_id"]}
    assert normal["memory"]["memory_id"] in {item.memory_id for item in ranked}
    assert unrelated["memory"]["memory_id"] not in {item.memory_id for item in ranked}


async def test_upsert_key_uniqueness_duplicate_conflict_and_scope() -> None:
    memory = await _memory()
    created = await memory.async_propose(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", key="pet.oscar.breed"
    )
    conflict = await memory.async_propose(
        "alice", "Oscar is a Cockapoo.", "pets", "explicit", key="pet.oscar.breed"
    )
    assert conflict["status"] == "needs_resolution"
    assert conflict["candidate"]["memory_id"] == created["memory"]["memory_id"]
    updated = await memory.async_update(
        "alice", created["memory"]["memory_id"], content="Oscar is a Cockapoo.",
        expected_revision=conflict["candidate"]["revision"],
    )
    assert created["status"] == "created"
    assert updated.content == "Oscar is a Cockapoo."
    assert len(await memory.async_list("alice")) == 1
    household = await memory.async_propose(
        SHARED_HOUSEHOLD_SCOPE_ID,
        "The household dog is a Cavachon.",
        "pets",
        "explicit",
        key="pet.oscar.breed",
    )
    assert household["status"] == "created"
    existing = await memory.async_propose(
        "alice", "Oscar is a Cockapoo.", "pets", "explicit"
    )
    assert existing["status"] == "needs_resolution"
    assert existing["candidate"]["memory_id"] == created["memory"]["memory_id"]
    conflict = await memory.async_propose(
        "alice", "Oscar is a Labrador.", "pets", "explicit", subject="Oscar", key="pet.oscar.breed"
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


async def test_upsert_preserves_omitted_metadata_for_key_and_duplicate() -> None:
    memory = await _memory()
    valid_from = "2025-01-01T00:00:00+00:00"
    created = await memory.async_propose(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
        valid_from=valid_from,
    )

    updated = await memory.async_update(
        "alice",
        created["memory"]["memory_id"],
        "Oscar is a Cavapoo.",
        "pets",
        source="explicit",
        key="pet.oscar.breed",
    )
    assert updated.subject == "Oscar"
    assert updated.valid_from == valid_from

    existing = await memory.async_propose(
        "alice", "Oscar is a Cavapoo.", "pets", "explicit"
    )
    assert existing["status"] == "needs_resolution"
    assert existing["candidate"]["subject"] == "Oscar"
    assert existing["candidate"]["key"] == "pet.oscar.breed"
    assert existing["candidate"]["valid_from"] == valid_from


async def test_upsert_replaces_explicitly_supplied_metadata() -> None:
    memory = await _memory()
    created = await memory.async_propose(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
        valid_from="2025-01-01T00:00:00+00:00",
    )

    updated = await memory.async_update(
        "alice",
        created["memory"]["memory_id"],
        "Oscar is a Cavapoo.",
        "pets",
        source="explicit",
        subject="Oscar James",
        key="pet.oscar.breed",
        valid_from="2026-01-01T00:00:00+00:00",
    )
    assert updated.subject == "Oscar James"
    assert updated.valid_from == "2026-01-01T00:00:00+00:00"


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


async def test_hybrid_embeddings_use_separate_model_fingerprinted_cache() -> None:
    durable = FakeStorage()
    cache = FakeStorage()
    memory = PersistentMemory(durable, cache)
    await memory.async_initialize()
    created = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit", subject="Oscar"
    )

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "What breed is my dog?")
    assert "embedding" not in durable.data["memories"][0]
    entry = cache.data["embeddings"][created["memory"]["memory_id"]]
    assert entry["model"] == "test-model"
    assert len(entry["fingerprint"]) == 64
    assert entry["vector"] == [1.0, 0.0]


async def test_legacy_inline_embeddings_are_removed_from_durable_storage() -> None:
    durable = FakeStorage()
    memory = PersistentMemory(durable)
    await memory.async_initialize()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    durable.data["memories"][0]["embedding"] = [1.0, 0.0]

    reloaded = PersistentMemory(durable, FakeStorage())
    await reloaded.async_initialize()

    assert "embedding" not in durable.data["memories"][0]
    assert (await reloaded.async_list("alice"))[0].content == "Oscar is a Cavachon."


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", "Oscar is a Cavapoo."),
        ("category", "animals"),
        ("subject", "Oscar James"),
        ("key", "pet.oscar.description"),
    ],
)
async def test_embedding_relevant_updates_invalidate_cache(
    field: str, value: str
) -> None:
    cache = FakeStorage()
    memory = PersistentMemory(FakeStorage(), cache)
    await memory.async_initialize()
    created = await memory.async_add(
        "alice",
        "Oscar is a Cavachon.",
        "pets",
        "explicit",
        subject="Oscar",
        key="pet.oscar.breed",
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await memory.async_prepare_hybrid(["alice"], "breed")
    calls.clear()
    await memory.async_update("alice", created["memory"]["memory_id"], **{field: value})
    await memory.async_prepare_hybrid(["alice"], "breed")
    assert len(calls) == 2
    assert len(calls[0]) == 1
    assert calls[1] == ["breed"]


async def test_valid_from_update_keeps_cached_embedding() -> None:
    memory = PersistentMemory(FakeStorage(), FakeStorage())
    await memory.async_initialize()
    created = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit"
    )
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    await memory.async_prepare_hybrid(["alice"], "breed")
    calls.clear()
    await memory.async_update(
        "alice", created["memory"]["memory_id"], valid_from="2026-01-01T00:00:00Z"
    )
    await memory.async_prepare_hybrid(["alice"], "breed")
    assert calls == [["breed"]]


async def test_hybrid_embedding_generation_is_batched() -> None:
    memory = PersistentMemory(FakeStorage(), FakeStorage())
    await memory.async_initialize()
    total = EMBEDDING_CACHE_BATCH_SIZE * 2 + 3
    for index in range(total):
        unique_token = "".join(
            chr(ord("a") + digit)
            for digit in (index // (26 * 26), (index // 26) % 26, index % 26)
        )
        await memory.async_add(
            "alice",
            unique_token,
            "preferences",
            "explicit",
        )
    batch_sizes: list[int] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        batch_sizes.append(len(inputs))
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "preferences")
    assert batch_sizes == [
        EMBEDDING_CACHE_BATCH_SIZE,
        EMBEDDING_CACHE_BATCH_SIZE,
        3,
        1,
    ]


async def test_hybrid_batch_failure_preserves_completed_cache_progress() -> None:
    cache = FakeStorage()
    memory = PersistentMemory(FakeStorage(), cache)
    await memory.async_initialize()
    total = EMBEDDING_CACHE_BATCH_SIZE + 2
    for index in range(total):
        unique_token = "".join(
            chr(ord("a") + digit)
            for digit in (index // (26 * 26), (index // 26) % 26, index % 26)
        )
        await memory.async_add("alice", unique_token, "preferences", "explicit")
    calls = 0

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("provider batch failed")
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "preferences") is None
    assert len(cache.data["embeddings"]) == EMBEDDING_CACHE_BATCH_SIZE


async def test_embedding_model_change_regenerates_cached_vectors() -> None:
    memory = PersistentMemory(FakeStorage(), FakeStorage())
    await memory.async_initialize()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")
    calls: list[list[str]] = []

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        calls.append(inputs)
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "first-model")
    await memory.async_prepare_hybrid(["alice"], "breed")
    calls.clear()
    memory.set_embedding_provider(embeddings, "second-model")
    await memory.async_prepare_hybrid(["alice"], "breed")
    assert len(calls) == 2


async def test_hybrid_cache_failure_falls_back_without_harming_memory() -> None:
    durable = FakeStorage()
    memory = PersistentMemory(durable, FailingCacheStorage())
    await memory.async_initialize()
    await memory.async_add("alice", "Oscar is a Cavachon.", "pets", "explicit")

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    assert await memory.async_prepare_hybrid(["alice"], "breed") is None
    assert (await memory.async_list("alice"))[0].content == "Oscar is a Cavachon."
    assert "embedding" not in durable.data["memories"][0]


async def test_hybrid_relevance_gate_accepts_semantic_and_rejects_unrelated() -> None:
    memory = PersistentMemory(FakeStorage(), FakeStorage())
    await memory.async_initialize()
    oscar = await memory.async_add(
        "alice", "Oscar is a Cavachon.", "pets", "explicit"
    )
    heating = await memory.async_add(
        "alice", "User dislikes very warm rooms.", "preferences", "explicit"
    )
    await memory.async_add(
        "alice", "Passport expires in 2028.", "travel", "explicit"
    )
    await memory.async_add(
        "alice", "User prefers horror games.", "preferences", "explicit"
    )

    def vector(text: str) -> list[float]:
        if "Oscar" in text or "breed" in text:
            return [1.0, 0.0, 0.0, 0.0]
        if "warm rooms" in text or "temperature" in text:
            return [0.0, 1.0, 0.0, 0.0]
        if "kitchen lights" in text:
            return [0.0, 0.0, 0.0, 1.0]
        return [0.0, 0.0, 1.0, 0.0]

    async def embeddings(inputs: list[str]) -> list[list[float]]:
        return [vector(text) for text in inputs]

    memory.set_embedding_provider(embeddings, "test-model")
    dog_query = await memory.async_prepare_hybrid(["alice"], "What breed is my dog?")
    assert [
        item.memory_id
        for item in await memory.async_search(
            "alice", "What breed is my dog?", hybrid=True, query_embedding=dog_query
        )
    ] == [oscar["memory"]["memory_id"]]
    heat_query = await memory.async_prepare_hybrid(
        ["alice"], "What temperature should I set the heating to?"
    )
    assert heating["memory"]["memory_id"] in {
        item.memory_id
        for item in await memory.async_search(
            "alice",
            "What temperature should I set the heating to?",
            hybrid=True,
            query_embedding=heat_query,
        )
    }
    unrelated_query = await memory.async_prepare_hybrid(
        ["alice"], "Turn the kitchen lights off"
    )
    assert (
        await memory.async_search(
            "alice",
            "Turn the kitchen lights off",
            hybrid=True,
            query_embedding=unrelated_query,
        )
        == []
    )


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
    scope_token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), scope=user_scope("alice", source="test"))
    )
    session_token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), memory_session=("conversation:first", 5))
    )
    try:
        assert await entity._async_retrieve_memories(context, "tea") == [first]
        current["one"] = MemoryRecord(
            "one", "alice", "Alice likes coffee.", "preferences", "explicit", "a", "b"
        )
        second = await entity._async_retrieve_memories(context, "unrelated topic")
        assert second[0].content == "Alice likes coffee."
        assert memory.async_search.await_count == 1
        _ACTIVE_TURN.reset(session_token)
        session_token = _ACTIVE_TURN.set(
            replace(_ACTIVE_TURN.get(), memory_session=("conversation:second", 5))
        )
        await entity._async_retrieve_memories(context, "tea")
        assert memory.async_search.await_count == 2
    finally:
        _ACTIVE_TURN.reset(session_token)
        _ACTIVE_TURN.reset(scope_token)


def test_scope_composition_and_strict_write_destinations() -> None:
    entity = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    entity.subentry = SimpleNamespace(
        data={CONF_SHARED_MEMORY_MODE: SHARED_MEMORY_EXPLICIT}
    )
    context = SimpleNamespace(context=SimpleNamespace(user_id="alice"))

    token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), scope=user_scope("alice", source="test"))
    )
    try:
        assert entity._current_readable_memory_scope_ids(context) == [
            "alice",
            SHARED_HOUSEHOLD_SCOPE_ID,
        ]
        assert (
            entity._current_write_memory_scope_id(None, context, intent="user_request")
            == "alice"
        )
        assert (
            entity._current_write_memory_scope_id(
                "household", context, intent="user_request"
            )
            == SHARED_HOUSEHOLD_SCOPE_ID
        )
    finally:
        _ACTIVE_TURN.reset(token)

    token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), scope=shared_scope(source="test"))
    )
    try:
        assert entity._current_readable_memory_scope_ids(context) == [
            SHARED_HOUSEHOLD_SCOPE_ID
        ]
        with pytest.raises(ValueError, match="cannot write personal"):
            entity._current_write_memory_scope_id(
                "personal", context, intent="user_request"
            )
    finally:
        _ACTIVE_TURN.reset(token)

    token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), scope=unretained_scope())
    )
    try:
        assert entity._current_readable_memory_scope_ids(context) == []
    finally:
        _ACTIVE_TURN.reset(token)


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


@pytest.mark.parametrize(
    ("inflected", "base"),
    [
        ("travelling", "travel"),
        ("traveling", "travel"),
        ("cancelling", "cancel"),
        ("canceling", "cancel"),
        ("labelling", "label"),
        ("controlling", "control"),
        ("modelling", "model"),
        ("signalling", "signal"),
        ("levelling", "level"),
        ("channelling", "channel"),
        ("counselling", "counsel"),
        ("fuelling", "fuel"),
        ("dialling", "dial"),
        ("gassing", "gas"),
        ("bussing", "bus"),
        ("quizzing", "quiz"),
        ("calling", "call"),
        ("falling", "fall"),
        ("filling", "fill"),
        ("rolling", "roll"),
        ("spelling", "spell"),
        ("pressing", "press"),
        ("dressing", "dress"),
        ("buzzing", "buzz"),
        ("passing", "pass"),
        ("fussing", "fuss"),
        ("running", "run"),
        ("shopping", "shop"),
        ("compelling", "compel"),
        ("excelling", "excel"),
        ("libelling", "libel"),
        ("marshalling", "marshal"),
        ("panelling", "panel"),
        ("parcelling", "parcel"),
        ("patrolling", "patrol"),
        ("pencilling", "pencil"),
        ("propelling", "propel"),
        ("rebelling", "rebel"),
        ("repelling", "repel"),
        ("rivalling", "rival"),
        ("spiralling", "spiral"),
        ("squirrelling", "squirrel"),
        ("surveilling", "surveil"),
        ("tunnelling", "tunnel"),
        ("unravelling", "unravel"),
        ("installing", "install"),
        ("selling", "sell"),
        ("spilling", "spill"),
        ("pulling", "pull"),
        ("dwelling", "dwell"),
        ("appalling", "appall"),
        ("corralling", "corral"),
        ("dispelling", "dispel"),
        ("expelling", "expel"),
        ("extolling", "extol"),
        ("grovelling", "grovel"),
        ("impelling", "impel"),
        ("initialling", "initial"),
        ("pummelling", "pummel"),
        ("relabelling", "relabel"),
        ("remodelling", "remodel"),
        ("shovelling", "shovel"),
        ("swivelling", "swivel"),
        ("fulfilling", "fulfil"),
        ("distilling", "distil"),
        ("enrolling", "enrol"),
    ],
)
def test_memory_stemming_maintains_ambiguous_inflection_families(
    inflected, base
):
    from custom_components.extended_openai_conversation_responses.memory import _stem
    assert _stem(inflected) == _stem(base)


@pytest.mark.parametrize(
    ("content", "query"),
    [
        ("I enjoy travelling.", "enjoy travel holidays"),
        ("I enjoy travelling.", "enjoy travel trips"),
        ("I prefer gassing up at the motorway services.", "gas"),
        ("I spend an hour bussing to work.", "bus"),
        ("My dog is patrolling the garden.", "patrol garden checking"),
        ("I find compelling stories interesting.", "compel stories reasoning"),
        ("I plan to expel the member.", "expelling member actions"),
        ("I am distilling clean water.", "distil water steps"),
        ("We fulfil orders daily.", "fulfilling order dispatch"),
        ("I intend to enrol students.", "enrolling student lessons"),
        ("I am corralling the sheep.", "corral sheep notes"),
    ],
)
async def test_memory_stem_regressions_restore_actual_lexical_retrieval(
    content, query
):
    memory = await _memory()
    added = await memory.async_add("alice", content, "personal", "explicit")
    results = await memory.async_search("alice", query)
    assert any(
        record.memory_id == added["memory"]["memory_id"] for record in results
    )


async def test_memory_related_stems_preserve_both_proposed_facts():
    memory = await _memory()
    original = await memory.async_add(
        "alice", "My hobby is travelling.", "hobbies", "explicit"
    )
    outcome = await memory.async_propose(
        "alice", "My hobby is travel.", "hobbies", "explicit"
    )
    assert outcome["status"] == "created"
    assert outcome["memory"]["memory_id"] != original["memory"]["memory_id"]
    assert len(await memory.async_list("alice")) == 2

@pytest.mark.parametrize(
    ("stored", "incoming"),
    [
        ("My dog patrols the garden.", "My dog is patrolling the garden."),
        ("I plan to expel the member.", "I plan on expelling the member."),
        ("We distil water.", "We are distilling water."),
        ("I fulfil orders.", "I am fulfilling orders."),
        ("I enrol clients.", "I am enrolling clients."),
        ("My job is to fulfil orders.", "My job involves fulfilling orders."),
        ("We fulfill orders.", "We are fulfilling orders."),
        (
            "Our neighbour rebels against restrictions.",
            "Our neighbour is rebelling against restrictions.",
        ),
    ],
)
async def test_memory_l_stems_do_not_mutate_existing_proposed_facts(
    stored, incoming
):
    memory = await _memory()
    original = await memory.async_add("alice", stored, "personal", "explicit")
    outcome = await memory.async_propose("alice", incoming, "personal", "explicit")
    assert outcome["status"] == "created"
    assert outcome["memory"]["memory_id"] != original["memory"]["memory_id"]
    assert len(await memory.async_list("alice")) == 2


@pytest.mark.parametrize(
    ("inflected", "different"),
    [
        ("patrolling", "patroll"),
        ("calling", "cal"),
        ("spelling", "spel"),
        ("installing", "instal"),
        ("bussing", "buss"),
        ("buzzing", "buz"),
    ],
)
def test_memory_stem_does_not_strip_genuine_or_unknown_word_endings(
    inflected, different
):
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert _stem(inflected) != _stem(different)


async def test_memory_stemming_does_not_create_unrelated_upsert_conflicts():
    memory = await _memory()
    await memory.async_add("alice", "My dog patrols the garden.", "pets", "explicit")
    outcome = await memory.async_propose(
        "alice", "My dog watches the television.", "pets", "explicit"
    )
    assert outcome["status"] == "created"
    assert len(await memory.async_list("alice")) == 2

@pytest.mark.parametrize(
    ("british", "american", "participial"),
    [
        ("fulfil", "fulfill", "fulfilling"),
        ("distil", "distill", "distilling"),
        ("enrol", "enroll", "enrolling"),
    ],
)
def test_memory_english_single_l_spelling_aliases_are_bidirectional(
    british, american, participial
):
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert _stem(british) == _stem(american) == _stem(participial)
    assert _stem(british + "s") == _stem(american + "s") == _stem(american)


@pytest.mark.parametrize(
    ("british", "american"),
    [
        ("fulfilment", "fulfillment"),
        ("enrolment", "enrollment"),
    ],
)
def test_memory_english_derivative_spelling_variants_are_exact(
    british, american
):
    from custom_components.extended_openai_conversation_responses.memory import _stem

    assert _stem(british) == _stem(american)
    assert _stem(british + "s") == _stem(american + "s") == _stem(american)


@pytest.mark.parametrize(
    ("source", "new_content"),
    [
        ("I want to expel the member.", "I want to explore the member."),
        ("We intend to distil water.", "We intend to distribute water."),
        ("I fulfil orders.", "I cancel orders."),
    ],
)
async def test_memory_spelling_repairs_leave_unrelated_upserts_distinct(
    source, new_content
):
    memory = await _memory()
    original = await memory.async_add("alice", source, "personal", "explicit")
    outcome = await memory.async_propose("alice", new_content, "personal", "explicit")
    assert outcome["status"] == "created"
    assert len(await memory.async_list("alice")) == 2
    assert outcome["memory"]["memory_id"] != original["memory"]["memory_id"]



def test_memory_phrase_normalization_equates_straight_and_curly_apostrophes():
    from custom_components.extended_openai_conversation_responses.memory import (
        _normalize,
    )

    assert _normalize("What's the WiFi password") == _normalize(
        "What’s the WiFi password"
    )
    assert _normalize("don't") == _normalize("don’t")
