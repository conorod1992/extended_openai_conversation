"""Proposals preserve existing facts; only exact identities require resolution."""

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
async def test_different_unkeyed_proposal_does_not_mutate_old_fact(
    existing, incoming
):
    storage = FakeStorage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    created = await memory.async_propose("alice", existing, "personal", "explicit")
    result = await memory.async_propose("alice", incoming, "preferences", "explicit")
    assert result["status"] == "created"
    records = await memory.async_list("alice")
    assert len(records) == 2
    retained = next(r for r in records if r.memory_id == created["memory"]["memory_id"])
    assert retained.content == existing
    assert retained.category == "personal"


async def test_literal_proposal_duplicates_noop_and_key_collisions_do_not_write():
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    first = await memory.async_propose(
        "alice", "I prefer tea to coffee.", "preferences", "explicit"
    )
    duplicate = await memory.async_propose(
        "alice", "I prefer tea to coffee.", "preferences", "explicit"
    )
    assert duplicate["status"] == "unchanged"
    assert duplicate["memory"]["memory_id"] == first["memory"]["memory_id"]
    punctuation_change = await memory.async_propose(
        "alice", "I prefer tea to coffee!!!", "preferences", "explicit"
    )
    assert punctuation_change["status"] == "created"
    assert any(r.content == "I prefer tea to coffee." for r in await memory.async_list("alice"))
    keyed = await memory.async_propose(
        "bob",
        "I prefer tea to coffee.",
        "preferences",
        "explicit",
        key="drink.preference",
    )
    replaced = await memory.async_propose(
        "bob",
        "I prefer coffee to tea.",
        "preferences",
        "explicit",
        key="drink.preference",
    )
    assert replaced["status"] == "needs_resolution"
    assert replaced["candidate"]["memory_id"] == keyed["memory"]["memory_id"]
    assert replaced["candidate"]["content"] == "I prefer tea to coffee."


async def test_duplicate_detection_does_not_build_overlap_sets(monkeypatch):
    memory = PersistentMemory(FakeStorage())
    await memory.async_initialize()
    await memory.async_add(
        "alice", "I prefer tea to coffee.", "preferences", "explicit"
    )

    def unexpected_tokens(_content):
        raise AssertionError("Duplicate equality requires no token sets")

    monkeypatch.setattr(module, "_cached_memory_tokens", unexpected_tokens)
    assert memory._find_duplicate("alice", "I prefer tea to coffee.") is not None
    assert memory._find_duplicate("alice", "I prefer tea to coffee!!!") is None
    assert memory._find_duplicate("alice", "I prefer coffee to tea.") is None
    assert memory._find_duplicate("bob", "I prefer tea to coffee.") is None
