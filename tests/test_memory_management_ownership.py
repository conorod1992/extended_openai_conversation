"""Ownership regressions for persistent-memory management browsing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from inspect import signature
from typing import Any

from custom_components.extended_openai_conversation_responses.management_browser import (
    async_browse_memories,
)
from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
)


class _Storage:
    """Minimal detached durable store."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self.data = {"memories": [asdict(record) for record in records]}

    async def async_load(self) -> dict[str, Any]:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        self.data = deepcopy(data)


def _record(
    memory_id: str,
    owner: str,
    content: str,
    category: str,
    updated_at: str,
    *,
    source: str = "explicit",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id=owner,
        content=content,
        category=category,
        source=source,
        created_at=updated_at,
        updated_at=updated_at,
        last_confirmed_at=updated_at,
    )


async def test_management_browse_is_owned_by_persistent_memory() -> None:
    """Management search keeps exact matching/order/paging behind a public boundary."""
    memory = PersistentMemory(
        _Storage(
            [
                _record(
                    "older",
                    "user:alice",
                    "Oscar likes walks",
                    "pets",
                    "2026-09-01T10:00:00+00:00",
                ),
                _record(
                    "newer",
                    "user:alice",
                    "Oscar likes chicken",
                    "pets",
                    "2026-09-02T10:00:00+00:00",
                ),
                _record(
                    "other",
                    "user:bob",
                    "Oscar has a red lead",
                    "pets",
                    "2026-09-03T10:00:00+00:00",
                ),
            ]
        )
    )
    await memory.async_initialize()

    result = await async_browse_memories(
        memory,
        "user:alice",
        "user:alice",
        {"action": "search", "query": "OSCAR", "limit": 1, "offset": 0},
        include_scope=False,
    )

    assert [item["memory_id"] for item in result["memories"]] == ["newer"]
    assert result["total"] == 2
    assert result["has_more"] is True
    assert result["query"] == "OSCAR"

    second = await async_browse_memories(
        memory,
        "user:alice",
        "user:alice",
        {"action": "search", "query": "oscar", "limit": 1, "offset": 1},
        include_scope=False,
    )
    assert [item["memory_id"] for item in second["memories"]] == ["older"]
    assert second["has_more"] is False


async def test_management_browse_preserves_category_and_source_matching() -> None:
    """The moved browse projection keeps the former content/category/source contract."""
    memory = PersistentMemory(
        _Storage(
            [
                _record(
                    "category",
                    "user:alice",
                    "Unrelated content",
                    "Driving Preferences",
                    "2026-09-01T10:00:00+00:00",
                ),
                _record(
                    "source",
                    "user:alice",
                    "Another fact",
                    "misc",
                    "2026-09-02T10:00:00+00:00",
                    source="implicit",
                ),
            ]
        )
    )
    await memory.async_initialize()

    category, category_total = await memory.async_browse(
        "user:alice", "driving preferences"
    )
    source, source_total = await memory.async_browse("user:alice", "implicit")

    assert [item.memory_id for item in category] == ["category"]
    assert category_total == 1
    assert [item.memory_id for item in source] == ["source"]
    assert source_total == 1


def test_test_only_embedding_scheduler_seam_is_retired() -> None:
    """PersistentMemory no longer exposes lifecycle state production never supplied."""
    assert "embedding_task_scheduler" not in signature(PersistentMemory).parameters

    memory = PersistentMemory(_Storage([]))

    assert not hasattr(memory, "_embedding_task_scheduler")
    assert not hasattr(memory, "_embedding_maintenance_requested")
