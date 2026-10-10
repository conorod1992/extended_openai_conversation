"""Search precision and recall regressions from audit PR 7."""

import pytest

from tests.test_knowledge import FakeStorage as KnowledgeStorage, _library
from tests.test_memory import FakeStorage as MemoryStorage, _memory


async def test_compound_expansion_does_not_hide_memory_in_longer_request():
    storage = MemoryStorage()
    memory = await _memory(storage)
    relevant = await memory.async_add(
        "alice", "The user's calibration token is cobalt-zebra.", "preferences", "explicit"
    )
    await memory.async_add("alice", "The user's favourite soup is minestrone.", "food", "explicit")
    await memory.async_add("bob", "The user's calibration token is amber-lynx.", "preferences", "explicit")
    query = "What is my calibration token? Remember that my project codename is lattice-quartz."
    for current in (memory, await _memory(storage)):
        assert [item.memory_id for item in await current.async_search("alice", query)] == [relevant["memory"]["memory_id"]]
        assert (await current.async_search("alice", "zebra"))[0].memory_id == relevant["memory"]["memory_id"]


async def test_answer_passage_beats_title_only_opening_after_restart():
    storage = KnowledgeStorage()
    library = await _library(storage)
    content = (
        "General introduction and unrelated setup notes. " * 180
    ) + "\nThe router password is CORRECT-ANSWER."
    source = await library.async_create(
        "Router password", "Router password reference", content
    )
    for current in (library, await _library(storage)):
        results = await current.async_search("router password")
        assert results[0].source_id == source.source_id
        assert "CORRECT-ANSWER" in results[0].excerpt


@pytest.mark.parametrize("query", ["C++", "R", "7", "C#"])
async def test_short_identifiers_survive_memory_and_knowledge_reload(query):
    memories = MemoryStorage()
    knowledge = KnowledgeStorage()
    memory = await _memory(memories)
    library = await _library(knowledge)
    text = "My preferred languages are C++ and R. C# is also useful. Device number 7."
    await memory.async_add("alice", text, "home", "explicit")
    source = await library.async_create("Programming", "Reference", text)
    other = await library.async_create("Unrelated", "Unrelated", "Other material")
    for current in (memory, await _memory(memories)):
        assert (await current.async_search("alice", query))[0].content == text
        assert not await current.async_search("bob", query)
    for current in (library, await _library(knowledge)):
        assert (await current.async_search(query))[0].source_id == source.source_id
        assert not await current.async_search(query, [other.source_id])


@pytest.mark.parametrize("query", ["boiler", "USB", "power"])
async def test_compound_components_and_possessives_are_indexed(query):
    storage = KnowledgeStorage()
    library = await _library(storage)
    source = await library.async_create(
        "Manual",
        "Instructions",
        "Check the boiler's gauge. Use USB-C. Power-cycle the router.",
    )
    assert (await (await _library(storage)).async_search(query))[
        0
    ].source_id == source.source_id


async def test_punctuation_identifier_does_not_match_plain_letter():
    memory = await _memory()
    await memory.async_add("alice", "My language is C.", "home", "explicit")
    assert not await memory.async_search("alice", "C++")
    assert not await memory.async_search("alice", "R")


async def test_metadata_index_removed_when_source_changes_or_is_deleted():
    library = await _library()
    source = await library.async_create("UniqueTitle", "Metadata", "Unrelated passage")
    assert await library.async_search("UniqueTitle")
    await library.async_update(source.source_id, title="Other title")
    assert not await library.async_search("UniqueTitle")
    await library.async_delete(source.source_id)
    assert not await library.async_search("Other title")
