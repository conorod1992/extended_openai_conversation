"""Tests for complete Memory management browsing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_browser


class FakeMemory:
    """Small PersistentMemory-shaped fixture for management-only reads."""

    def __init__(self, records):
        self._memories = {record.memory_id: record for record in records}
        self.list_calls: list[tuple[str, object, int, int]] = []

    async def async_list(self, owner, category, limit, offset):
        self.list_calls.append((owner, category, limit, offset))
        records = [
            record
            for record in self._memories.values()
            if record.user_id == owner
            and (category is None or record.category == category)
        ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records[offset : offset + limit]


def record(index: int, *, owner: str = "user-a", content: str | None = None):
    return SimpleNamespace(
        memory_id=f"memory-{index}",
        user_id=owner,
        content=content or f"Memory {index}",
        category="general",
        source="explicit",
        created_at=f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
        updated_at=f"2026-02-{(index % 28) + 1:02d}T{index % 24:02d}:00:00+00:00",
        importance="normal",
        subject=None,
        key=None,
        valid_from=None,
        last_confirmed_at=None,
    )


@pytest.mark.asyncio
async def test_list_page_reports_authoritative_has_more() -> None:
    memory = FakeMemory([record(index) for index in range(101)])

    first = await management_browser._list_page(
        memory,
        "user-a",
        "user:user-a",
        {"limit": 100, "offset": 0},
        include_scope=False,
    )
    last = await management_browser._list_page(
        memory,
        "user-a",
        "user:user-a",
        {"limit": 100, "offset": 100},
        include_scope=False,
    )

    assert len(first["memories"]) == 100
    assert first["has_more"] is True
    assert len(last["memories"]) == 1
    assert last["has_more"] is False
    assert memory.list_calls[-1] == ("user-a", None, 100, 100)


def test_search_page_uses_complete_scope_not_first_list_page() -> None:
    records = [record(index) for index in range(150)]
    records[149] = record(149, content="The hidden marmalade preference")
    memory = FakeMemory(records)

    result = management_browser._search_page(
        memory,
        "user-a",
        "marmalade",
        limit=100,
        offset=0,
        include_scope=False,
    )

    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["memories"][0]["memory_id"] == "memory-149"


def test_search_page_is_bounded_and_paginated() -> None:
    memory = FakeMemory(
        [record(index, content=f"Matching preference {index}") for index in range(130)]
    )

    first = management_browser._search_page(
        memory,
        "user-a",
        "matching preference",
        limit=100,
        offset=0,
        include_scope=False,
    )
    second = management_browser._search_page(
        memory,
        "user-a",
        "matching preference",
        limit=100,
        offset=100,
        include_scope=False,
    )

    assert len(first["memories"]) == 100
    assert first["has_more"] is True
    assert len(second["memories"]) == 30
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_wrapper_keeps_scope_authorization_before_memory_access(monkeypatch) -> None:
    original = AsyncMock(return_value={"unexpected": True})
    wrapped = management_browser.wrap_management_browser(original)
    memory_get = AsyncMock()
    monkeypatch.setattr(management_browser, "async_get_memory", memory_get)
    monkeypatch.setattr(management_browser.management_ui, "entry_and_agent", lambda *args: (object(), object()))

    with pytest.raises(Exception, match="Unknown data scope"):
        await wrapped(
            None,
            "user-a",
            False,
            {
                "section": "memories",
                "action": "search",
                "entry_id": "entry",
                "subentry_id": "agent",
                "scope_id": "user:someone-else",
                "query": "private",
            },
        )

    memory_get.assert_not_awaited()
    original.assert_not_awaited()
