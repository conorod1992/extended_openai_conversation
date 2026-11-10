"""Knowledge retrieval must index supported word forms symmetrically."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)


class _Storage:
    """Keep the search tests independent of Home Assistant disk storage."""

    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)


@pytest.mark.parametrize(
    ("base", "inflected"),
    [
        ("bus", "buses"),
        ("gas", "gases"),
        ("run", "running"),
        ("hop", "hopping"),
        ("pet", "petting"),
        ("map", "mapping"),
    ],
)
async def test_knowledge_matches_supported_short_roots_in_both_directions(
    base: str, inflected: str
) -> None:
    library = KnowledgeLibrary(_Storage())
    await library.async_initialize()
    alternate = await library.async_create(inflected, "", "Reference information.")
    unrelated = await library.async_create("The guide", "", "How to use it.")

    assert [result.source_id for result in await library.async_search(base)] == [
        alternate.source_id
    ]
    exact = await library.async_create(base, "", "Reference information.")
    assert [result.source_id for result in await library.async_search(base)] == [
        exact.source_id,
        alternate.source_id,
    ]
    assert [result.source_id for result in await library.async_search(inflected)] == [
        alternate.source_id,
        exact.source_id,
    ]
    assert unrelated.source_id not in {
        result.source_id for result in await library.async_search(f"what is the {base}")
    }
    assert unrelated.source_id in {
        result.source_id for result in await library.async_search("the")
    }

    await library.async_update(alternate.source_id, title="Unrelated")
    assert [result.source_id for result in await library.async_search(inflected)] == [
        exact.source_id
    ]
    await library.async_delete(exact.source_id)
    assert await library.async_search(inflected) == []


async def test_knowledge_short_roots_keep_identifiers_and_distinct_words_separate() -> (
    None
):
    library = KnowledgeLibrary(_Storage())
    await library.async_initialize()
    hop = await library.async_create("Hop", "", "Reference information.")
    await library.async_create("Hoping", "", "Reference information.")
    await library.async_create("hop_count", "", "Reference information.")
    await library.async_create("C++", "", "Reference information.")
    assert [result.source_id for result in await library.async_search("hopping")] == [
        hop.source_id
    ]
    assert await library.async_search("hopping_count") == []
    assert await library.async_search("C") == []
