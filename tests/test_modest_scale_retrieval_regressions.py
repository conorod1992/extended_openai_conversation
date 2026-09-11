"""Modest-scale structural regressions for Memory and Knowledge retrieval."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from custom_components.extended_openai_conversation_responses.knowledge import (
    MAX_CATALOG_LIMIT,
    MAX_EXCERPT_CHARACTERS,
    MAX_SEARCH_LIMIT as KNOWLEDGE_MAX_SEARCH_LIMIT,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MAX_SEARCH_LIMIT as MEMORY_MAX_SEARCH_LIMIT,
    PersistentMemory,
)

_TIMESTAMP = "2026-09-11T10:00:00+00:00"


class _MemoryStorage:
    def __init__(self, memories: list[dict[str, Any]]) -> None:
        self.data = {"memories": deepcopy(memories)}

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)


class _KnowledgeStorage:
    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self.data = {"sources": deepcopy(sources)}

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)


def _memory(index: int, content: str) -> dict[str, Any]:
    return {
        "memory_id": f"memory-{index:03d}",
        "user_id": "scale-user",
        "content": content,
        "category": "preferences",
        "source": "explicit",
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
        "importance": "normal",
        "subject": f"Routine note {index:03d}",
        "key": f"scale.note_{index:03d}",
        "valid_from": None,
        "last_confirmed_at": _TIMESTAMP,
    }


def _knowledge_source(index: int, *, enabled: bool = True) -> dict[str, Any]:
    sections = [
        (
            f"Reference section {section} for source {index}. "
            "This maintenance reference covers routine inspection, seasonal checks, "
            "equipment notes, and ordinary operating guidance. "
        )
        * 7
        for section in range(12)
    ]
    if index == 17:
        sections[6] += (
            "The cerulean greenhouse calibration procedure uses the cobalt reference "
            "fixture before the final sensor verification. "
        )
    return {
        "source_id": f"source-{index:03d}",
        "title": f"Operations reference {index:03d}",
        "description": f"Routine reference material for installation {index:03d}.",
        "content": "\n\n".join(sections),
        "created_at": _TIMESTAMP,
        "updated_at": f"2026-09-11T10:{index % 60:02d}:00+00:00",
        "enabled": enabled,
    }


async def test_memory_retrieval_stays_ranked_and_bounded_with_hundreds_of_records() -> None:
    """Obvious lexical matches survive a few hundred plausible competing memories."""
    memories = []
    for index in range(400):
        if index == 173:
            content = (
                "The cerulean greenhouse calibration must use the cobalt reference "
                "fixture before the routine verification pass."
            )
        elif index % 5 == 0:
            content = (
                f"Routine greenhouse irrigation calibration note {index}: inspect "
                "valves, gauges, and seasonal watering settings."
            )
        else:
            content = (
                f"Routine household preference note {index}: record ordinary room, "
                "lighting, schedule, and maintenance details."
            )
        memories.append(_memory(index, content))

    store = PersistentMemory(_MemoryStorage(memories))
    await store.async_initialize()

    focused = await store.async_search(
        "scale-user", "cerulean greenhouse calibration", limit=10
    )
    assert focused
    assert "memory-173" in [record.memory_id for record in focused[:3]]
    assert len({record.memory_id for record in focused}) == len(focused)

    broad = await store.async_search("scale-user", "routine", limit=10_000)
    assert len(broad) == MEMORY_MAX_SEARCH_LIMIT
    assert len({record.memory_id for record in broad}) == len(broad)


async def test_knowledge_retrieval_and_catalog_remain_bounded_at_modest_scale() -> None:
    """Chunk ranking, result caps, and catalog pagination hold at realistic scale."""
    enabled_sources = [_knowledge_source(index) for index in range(40)]
    disabled_sources = [
        _knowledge_source(index, enabled=False) for index in range(40, 44)
    ]
    library = KnowledgeLibrary(_KnowledgeStorage(enabled_sources + disabled_sources))
    await library.async_initialize()

    stats = library.stats()
    assert stats["knowledge_source_count"] == 44
    assert stats["knowledge_enabled_source_count"] == 40
    assert stats["knowledge_indexed_chunk_count"] >= 100

    focused = await library.async_search(
        "cerulean greenhouse calibration", limit=KNOWLEDGE_MAX_SEARCH_LIMIT
    )
    assert focused
    assert "source-017" in [result.source_id for result in focused[:3]]
    assert all(len(result.excerpt) <= MAX_EXCERPT_CHARACTERS for result in focused)
    assert len({result.source_id for result in focused}) == len(focused)

    broad = await library.async_search("reference", limit=10_000)
    assert len(broad) == KNOWLEDGE_MAX_SEARCH_LIMIT
    assert len({result.source_id for result in broad}) == len(broad)

    seen: list[str] = []
    offset = 0
    while True:
        page = await library.async_catalog(limit=7, offset=offset)
        seen.extend(source["source_id"] for source in page["sources"])
        assert page["returned"] <= 7
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] is not None
        assert page["next_offset"] > offset
        offset = page["next_offset"]

    assert len(seen) == 40
    assert len(set(seen)) == 40
    assert set(seen) == {f"source-{index:03d}" for index in range(40)}

    capped_catalog = await library.async_catalog(limit=10_000)
    assert capped_catalog["returned"] == min(40, MAX_CATALOG_LIMIT)
