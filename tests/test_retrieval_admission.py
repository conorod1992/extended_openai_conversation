"""Automatic admission and phrase precision regressions."""

from unittest.mock import patch

import pytest

from custom_components.extended_openai_conversation_responses import memory as module
from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.retrieval_text import (
    bounded_phrase,
    ordered_term_phrase,
)
from tests.test_hybrid_memory_cold_path import FakeStorage, _memory


@pytest.mark.parametrize(
    ("query", "text", "expected"),
    [
        ("living room", "living roommate", False),
        ("car park", "oscar parker", False),
        ("living room", "living roommate then living room", True),
        ("caf\u00e9", "d\u00e9caf\u00e9 caf\u00e9", True),
        ("wifi", "wifi-router", False),
        ("owner's", "owner's manual", True),
        ("\u5ba2\u5385", "\u8bf7\u6253\u5f00\u5ba2\u5385\u706f", True),
        ("\uac70\uc2e4", "\uac70\uc2e4\uc5d0\uc11c", True),
        ("\u5c45\u9593", "\u5c45\u9593\u3067\u3059", True),
    ],
)
def test_bounded_phrase(query, text, expected):
    assert bounded_phrase(query, text) is expected


def test_stem_phrase_uses_whole_ordered_terms():
    assert not ordered_term_phrase(["car", "park"], ["oscar", "parker"])
    assert ordered_term_phrase(["live", "room"], ["live", "roommate", "live", "room"])
    assert not ordered_term_phrase(["room", "live"], ["live", "room"])


async def test_memory_phrase_bonus_not_awarded_to_roommate():
    memory = await _memory()
    await memory.async_add("alice", "Living roommate likes tea", "test", "explicit")
    await memory.async_add("alice", "Living room has chairs", "test", "explicit")
    with (
        patch.object(module, "_bm25_score", return_value=0.1),
        patch.object(module, "_metadata_bonus", return_value=0),
        patch.object(module, "_fuzzy_relevance", return_value=0),
    ):
        results = await memory.async_search("alice", "living room")
        assert [r.content for r in results] == [
            "Living room has chairs",
            "Living roommate likes tea",
        ]
    memory = await _memory()
    await memory.async_add(
        "alice", "Oscar Parker is our accountant.", "test", "explicit"
    )
    assert await memory.async_search("alice", "car park") == []


@pytest.mark.parametrize(
    ("query", "content"),
    [("turn on the lamp", "Allergic to lamb"), ("living room", "Likes jazz")],
)
async def test_automatic_rejects_fuzzy_only_explicit_retains_it(query, content):
    memory = await _memory()
    await memory.async_add("alice", content, "test", "explicit")
    assert await memory.async_search("alice", query)
    assert await memory.async_search("alice", query, allow_fuzzy_only=False) == []


@pytest.mark.parametrize(
    ("query", "content"),
    [("wifii", "WiFi password is secret"), ("cavchon puppy", "Oscar is a Cavachon")],
)
async def test_explicit_typo_search_remains_available(query, content):
    memory = await _memory()
    await memory.async_add("alice", content, "test", "explicit")
    assert await memory.async_search("alice", query)


async def test_automatic_semantic_only_including_tokenless_and_pagination():
    memory = await _memory()
    await memory.async_add("alice", "Allergic to lamb", "test", "explicit")
    await memory.async_add("bob", "Private unrelated fact", "test", "explicit")
    await memory.async_add("alice", "Lamp is in the kitchen", "test", "explicit")

    async def embeddings(inputs):
        return [[1.0, 0.0] for _ in inputs]

    memory.set_embedding_provider(embeddings)
    vector = await memory.async_prepare_hybrid(["alice"], "!!!")
    results = await memory.async_search(
        "alice", "!!!", query_embedding=vector, hybrid=True, allow_fuzzy_only=False
    )
    assert len(results) == 2
    assert all(record.user_id == "alice" for record in results)
    assert (
        await memory.async_search(
            "alice",
            "!!!",
            limit=1,
            offset=1,
            query_embedding=vector,
            hybrid=True,
            allow_fuzzy_only=False,
        )
        == results[1:]
    )
    results = await memory.async_search(
        "alice", "turn on the lamp", limit=1, allow_fuzzy_only=False
    )
    assert len(results) == 1 and results[0].content == "Lamp is in the kitchen"


async def test_knowledge_genuine_phrase_ranks_above_embedded_phrase():
    library = KnowledgeLibrary(FakeStorage())
    await library.async_initialize()
    embedded = await library.async_create(
        "Living roommate", "Living roommate", "Living roommate likes tea"
    )
    genuine = await library.async_create(
        "Living room", "Living room", "Living room has chairs"
    )
    results = await library.async_search("living room")
    assert results[0].source_id == genuine.source_id
    assert embedded.source_id in {result.source_id for result in results}
