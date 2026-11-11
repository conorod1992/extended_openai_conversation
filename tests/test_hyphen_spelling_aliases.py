"""Only established spelling pairs share lexical retrieval terms."""

import pytest

from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
    _tokens,
)
from custom_components.extended_openai_conversation_responses.retrieval_text import (
    lexical_terms,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_archive import _archive
from tests.test_knowledge_morphology_regressions import _Storage as KnowledgeStorage
from tests.test_memory_search_performance import _record, _Storage

PAIRS = [("wi-fi", "wifi"), ("e-mail", "email"), ("set-up", "setup")]
DIRECTIONS = PAIRS + [(b, a) for a, b in PAIRS]


@pytest.mark.parametrize("stored,query", DIRECTIONS)
@pytest.mark.parametrize("field", ["title", "description", "content"])
async def test_knowledge_aliases_in_each_indexed_field(stored, query, field):
    library = KnowledgeLibrary(KnowledgeStorage())
    await library.async_initialize()
    values = {"title": "Reference", "description": "Details", "content": "Instructions"}
    values[field] = f"The {stored} guide"
    alias = await library.async_create(**values)
    assert [r.source_id for r in await library.async_search(query)] == [alias.source_id]
    values[field] = f"The {query} guide"
    exact = await library.async_create(**values)
    assert [r.source_id for r in await library.async_search(query)] == [
        exact.source_id,
        alias.source_id,
    ]


@pytest.mark.parametrize("stored,query", DIRECTIONS)
@pytest.mark.parametrize("field", ["content", "subject", "key"])
async def test_memory_aliases_and_literal_preference(stored, query, field):
    def record(identity, term):
        return _record(
            identity,
            "alice",
            term if field == "content" else "Reference",
            "home",
            **({field: term} if field != "content" else {}),
        )

    manager = PersistentMemory(_Storage([record("alias", stored)]))
    await manager.async_initialize()
    assert [r.memory_id for r in await manager.async_search("alice", query)] == [
        "alias"
    ]
    manager = PersistentMemory(
        _Storage([record("alias", stored), record("exact", query)])
    )
    await manager.async_initialize()
    assert [r.memory_id for r in await manager.async_search("alice", query)] == [
        "exact",
        "alias",
    ]


@pytest.mark.parametrize("stored,query", DIRECTIONS)
async def test_archive_aliases_select_matching_excerpt(stored, query):
    archive = await _archive()
    session = await archive.async_begin_session(
        "one",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    for identity, term in [("alias", stored), ("exact", query)]:
        await archive.async_record_turn(
            session.session_id,
            run_id=identity,
            user_text="Remember this",
            assistant_text="Walls are white. " * 150
            + f"The {term} instructions are in the blue drawer.",
            successful=True,
        )
    results = (await archive.async_search("user:alice", query))["results"]
    assert len(results) == 2
    assert f"The {query} instructions" in results[0]["excerpt"]
    assert f"The {stored} instructions" in results[1]["excerpt"]
    assert all(len(r["excerpt"]) <= 502 for r in results)


@pytest.mark.parametrize(
    "compound,joined",
    [
        ("re-sign", "resign"),
        ("re-form", "reform"),
        ("co-op", "coop"),
        ("foo-bar", "foobar"),
    ],
)
def test_unrelated_compounds_do_not_gain_exact_terms(compound, joined):
    assert joined not in lexical_terms(compound)
    assert compound not in lexical_terms(joined)
    # Memory can still use its existing edit-distance fallback; no stronger token match.
    assert joined not in _tokens(compound)


def test_original_tokens_and_components_remain():
    assert lexical_terms("wi-fi") == ["wi-fi", "wifi", "wi", "fi"]
    assert lexical_terms("C++ C# x dog's 中文 running") == [
        "c++",
        "c#",
        "x",
        "dog's",
        "dog",
        "中文",
        "running",
    ]


@pytest.mark.parametrize("stored,query", DIRECTIONS)
async def test_knowledge_literal_content_precedes_alias_in_title(stored, query):
    library = KnowledgeLibrary(KnowledgeStorage())
    await library.async_initialize()
    alias = await library.async_create(stored, "", "Reference information")
    exact = await library.async_create("Reference", "", query)
    assert [r.source_id for r in await library.async_search(query)] == [
        exact.source_id,
        alias.source_id,
    ]
