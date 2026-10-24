"""Knowledge catalogue discovery preserves punctuation and short identifiers."""

import pytest

from tests.test_knowledge import FakeStorage, _entity, _library


@pytest.mark.parametrize("query", ["C", "C++", "C#"])
@pytest.mark.parametrize("field", ["title", "description"])
async def test_tool_catalog_keeps_requested_identifier_on_first_page(query, field):
    library = await _library()
    sources = {}
    for language in ("C", "C++", "C#"):
        title, description = (
            (f"{language} Guide", "Language reference")
            if field == "title"
            else ("Language guide", f"Reference for {language}")
        )
        sources[language] = await library.async_create(
            title, description, "Private body"
        )
    for index in range(20):
        await library.async_create(
            f"Recipes {index:02}", "Heating schedule", "Unrelated body"
        )
    await library.async_create(f"{query} Disabled", "", "Disabled body", enabled=False)
    agent = _entity(True, library.source_count)
    agent._knowledge = library

    page = await agent._async_execute_knowledge_tool("list", {"query": query})

    assert [source["source_id"] for source in page["sources"]] == [
        sources[query].source_id
    ]
    assert page["total"] == page["returned"] == 1
    assert page["has_more"] is False
    assert all("content" not in source for source in page["sources"])
    assert [source.source_id for source in await library.async_search(query)] == [
        sources[query].source_id
    ]


async def test_catalog_retains_partial_words_phrases_and_unicode_matching():
    library = await _library()
    recipes = await library.async_create(
        "Family Recipes", "O\u2019Brien\u2019s kitchen", "Body"
    )
    for query in ("cip", "MILY rec", "o'brien", "KITCH"):
        page = await library.async_catalog(query)
        assert [source["source_id"] for source in page["sources"]] == [
            recipes.source_id
        ]
    unicode_source = await library.async_create("Café 水温", "", "Body")
    for query in ("CAFE\u0301", "水"):
        page = await library.async_catalog(query)
        assert [source["source_id"] for source in page["sources"]] == [
            unicode_source.source_id
        ]


async def test_catalog_identifier_index_survives_reload_and_updates():
    storage = FakeStorage()
    library = await _library(storage)
    source = await library.async_create("C++ Guide", "", "Body")
    library = await _library(storage)
    assert (await library.async_catalog("c++"))["total"] == 1
    assert (await library.async_catalog("C#"))["total"] == 0
    await library.async_update(source.source_id, title="C# Guide")
    assert (await library.async_catalog("C++"))["total"] == 0
    assert (await library.async_catalog("c#"))["total"] == 1
