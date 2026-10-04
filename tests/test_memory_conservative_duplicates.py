"""Related unkeyed facts require resolution instead of suppressing corrections."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import memory as module
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from tests.test_memory_v2 import FakeStorage


@pytest.mark.parametrize(
    "existing,incoming",
    [
        ("I prefer tea to coffee.", "I prefer coffee to tea."),
        ("Alice lends books to Bob.", "Bob lends books to Alice."),
        ("I like coffee in the morning.", "I do not like coffee in the morning."),
        (
            "My thermostat should be set to 19 degrees.",
            "My thermostat should be set to 21 degrees.",
        ),
        ("I take 5 tablets every day.", "I take 6 tablets every day."),
    ],
)
async def test_changed_fact_requires_resolution_without_mutating_old_fact(
    existing, incoming
):
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    created = await memory.async_upsert("alice", existing, "personal", "explicit")
    snapshot = deepcopy(storage.data)
    result = await memory.async_upsert("alice", incoming, "preferences", "explicit")
    assert result["status"] == "needs_resolution"
    assert result["candidate"]["memory_id"] == created["memory"]["memory_id"]
    assert storage.data == snapshot
    records = await memory.async_list("alice")
    assert len(records) == 1
    assert records[0].content == existing
    assert records[0].category == "personal"


async def test_case_punctuation_variations_still_deduplicate_and_keys_replace():
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    first = await memory.async_upsert(
        "alice", "I prefer tea to coffee.", "preferences", "explicit"
    )
    duplicate = await memory.async_upsert(
        "alice", "I PREFER tea to coffee!!!", "preferences", "explicit"
    )
    assert duplicate["status"] == "unchanged"
    assert duplicate["memory"]["memory_id"] == first["memory"]["memory_id"]
    keyed = await memory.async_upsert(
        "bob",
        "I prefer tea to coffee.",
        "preferences",
        "explicit",
        key="drink.preference",
    )
    replaced = await memory.async_upsert(
        "bob",
        "I prefer coffee to tea.",
        "preferences",
        "explicit",
        key="drink.preference",
    )
    assert replaced["status"] == "updated"
    assert replaced["memory"]["memory_id"] == keyed["memory"]["memory_id"]
    assert replaced["memory"]["content"] == "I prefer coffee to tea."


async def test_duplicate_detection_does_not_build_overlap_sets(monkeypatch):
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    await memory.async_add(
        "alice", "I prefer tea to coffee.", "preferences", "explicit"
    )

    def unexpected_tokens(_content):
        raise AssertionError("Duplicate equality requires no token sets")

    monkeypatch.setattr(module, "_cached_memory_tokens", unexpected_tokens)
    assert memory._find_duplicate("alice", "I PREFER tea to coffee!!!") is not None
    assert memory._find_duplicate("alice", "I prefer coffee to tea.") is None
    assert memory._find_duplicate("bob", "I prefer tea to coffee.") is None
