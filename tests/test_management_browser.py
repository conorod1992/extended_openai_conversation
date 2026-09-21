"""Tests for complete Memory management browsing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_browser,
    management_ui,
)
from homeassistant.exceptions import HomeAssistantError


class FakeMemory:
    """Small PersistentMemory-shaped fixture for management-only reads."""

    def __init__(self, records):
        self._memories = {record.memory_id: record for record in records}
        self.list_calls: list[tuple[str, object, int, int]] = []

    async def async_list_page(self, owner, category, limit, offset):
        self.list_calls.append((owner, category, limit, offset))
        records = [
            record
            for record in self._memories.values()
            if record.user_id == owner
            and (category is None or record.category == category)
        ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        end = offset + limit
        return records[offset:end], len(records) > end

    async def async_browse(self, owner, query, limit=100, offset=0):
        folded = query.casefold()
        records = [
            record
            for record in self._memories.values()
            if record.user_id == owner
            and folded
            in " ".join(
                str(value or "")
                for value in (record.content, record.category, record.source)
            ).casefold()
        ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records[offset : offset + limit], len(records)


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
    assert memory.list_calls == [
        ("user-a", None, 100, 0),
        ("user-a", None, 100, 100),
    ]


@pytest.mark.asyncio
async def test_search_page_uses_complete_scope_not_first_list_page() -> None:
    records = [record(index) for index in range(150)]
    records[149] = record(149, content="The hidden marmalade preference")
    memory = FakeMemory(records)

    result = await management_browser.async_browse_memories(
        memory,
        "user-a",
        "user:user-a",
        {"action": "search", "query": "marmalade", "limit": 100, "offset": 0},
        include_scope=False,
    )

    assert result["total"] == 1
    assert result["has_more"] is False
    assert result["memories"][0]["memory_id"] == "memory-149"


@pytest.mark.asyncio
async def test_search_page_is_bounded_and_paginated() -> None:
    memory = FakeMemory(
        [record(index, content=f"Matching preference {index}") for index in range(130)]
    )

    first = await management_browser.async_browse_memories(
        memory,
        "user-a",
        "user:user-a",
        {
            "action": "search",
            "query": "matching preference",
            "limit": 100,
            "offset": 0,
        },
        include_scope=False,
    )
    second = await management_browser.async_browse_memories(
        memory,
        "user-a",
        "user:user-a",
        {
            "action": "search",
            "query": "matching preference",
            "limit": 100,
            "offset": 100,
        },
        include_scope=False,
    )

    assert len(first["memories"]) == 100
    assert first["has_more"] is True
    assert len(second["memories"]) == 30
    assert second["has_more"] is False


@pytest.mark.asyncio
async def test_command_keeps_scope_authorization_before_memory_access(
    hass, monkeypatch
) -> None:
    command = management_ui.async_management_command
    memory_get = AsyncMock()
    monkeypatch.setattr(management_ui, "async_get_memory", memory_get)
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *args: (object(), object()),
    )

    with pytest.raises(
        HomeAssistantError, match="This scope is not available to the current user"
    ):
        await command(
            hass,
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


async def test_non_browser_route_does_not_read_memories(
    hass, management_message, monkeypatch
):
    load = AsyncMock()
    monkeypatch.setattr(management_ui, "async_get_memory", load)
    with pytest.raises(HomeAssistantError, match="Unknown settings management action"):
        await management_ui.async_management_command(
            hass, "alice", True, management_message("settings", "invalid")
        )
    load.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_id", "subentry_id"),
    [
        (None, "agent-1"),
        ("entry-1", None),
        (123, "agent-1"),
        ("entry-1", 456),
    ],
)
async def test_command_rejects_missing_or_non_string_identifiers(
    hass,
    entry_id: object,
    subentry_id: object,
) -> None:
    command = management_ui.async_management_command

    with pytest.raises(
        HomeAssistantError, match="entry_id and subentry_id are required"
    ):
        await command(
            object(),
            "user-a",
            False,
            {
                "section": "memories",
                "action": "list",
                "entry_id": entry_id,
                "subentry_id": subentry_id,
            },
        )


@pytest.mark.asyncio
async def test_command_routes_list_through_selected_scope_and_memory(
    hass, monkeypatch
) -> None:
    command = management_ui.async_management_command
    memory = SimpleNamespace(async_browse=AsyncMock())
    message = {
        "section": "memories",
        "action": "list",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "scope_id": "user:someone",
        "limit": 25,
        "offset": 50,
    }

    entry_and_agent = Mock(return_value=(object(), object()))
    selected_scope = Mock(return_value="user:selected")
    memory_scope = Mock(return_value="selected-owner")
    get_memory = AsyncMock(return_value=memory)
    list_page = AsyncMock(return_value={"page": "list"})
    monkeypatch.setattr(management_ui, "entry_and_agent", entry_and_agent)
    monkeypatch.setattr(management_ui, "_selected_scope", selected_scope)
    monkeypatch.setattr(management_ui, "_memory_scope", memory_scope)
    monkeypatch.setattr(management_ui, "async_get_memory", get_memory)
    monkeypatch.setattr(management_browser, "_list_page", list_page)

    result = await command(hass, "admin-user", True, message)

    assert result == {"page": "list"}
    entry_and_agent.assert_called_once_with(hass, "entry-1", "agent-1")
    selected_scope.assert_called_once_with("admin-user", True, "user:someone")
    memory_scope.assert_called_once_with("user:selected")
    get_memory.assert_awaited_once_with(hass, "entry-1", "agent-1")
    list_page.assert_awaited_once_with(
        memory,
        "selected-owner",
        "user:selected",
        message,
        include_scope=True,
    )


@pytest.mark.asyncio
async def test_blank_search_falls_back_to_list_page(hass, monkeypatch) -> None:
    command = management_ui.async_management_command
    memory = SimpleNamespace(async_browse=AsyncMock())
    message = {
        "section": "memories",
        "action": "search",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "query": "   ",
    }

    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        Mock(return_value=(object(), object())),
    )
    monkeypatch.setattr(
        management_ui,
        "_selected_scope",
        Mock(return_value="user:user-a"),
    )
    monkeypatch.setattr(
        management_ui,
        "_memory_scope",
        Mock(return_value="user-a"),
    )
    monkeypatch.setattr(
        management_ui, "async_get_memory", AsyncMock(return_value=memory)
    )
    list_page = AsyncMock(return_value={"page": "fallback-list"})
    monkeypatch.setattr(management_browser, "_list_page", list_page)

    result = await command(hass, "user-a", False, message)

    assert result == {"page": "fallback-list"}
    list_page.assert_awaited_once_with(
        memory,
        "user-a",
        "user:user-a",
        message,
        include_scope=False,
    )
    memory.async_browse.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_routes_trimmed_query_with_bounded_paging(
    hass, monkeypatch
) -> None:
    command = management_ui.async_management_command
    memory = SimpleNamespace(async_browse=AsyncMock())
    message = {
        "section": "memories",
        "action": "search",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "query": "  Marmalade  ",
        "limit": management_browser.MAX_LIST_LIMIT + 500,
        "offset": -10,
    }

    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        Mock(return_value=(object(), object())),
    )
    monkeypatch.setattr(
        management_ui,
        "_selected_scope",
        Mock(return_value="user:user-a"),
    )
    monkeypatch.setattr(
        management_ui,
        "_memory_scope",
        Mock(return_value="user-a"),
    )
    monkeypatch.setattr(
        management_ui, "async_get_memory", AsyncMock(return_value=memory)
    )
    memory.async_browse.return_value = ([], 0)
    list_page = AsyncMock()
    monkeypatch.setattr(management_browser, "_list_page", list_page)

    result = await command(hass, "user-a", False, message)

    assert result == {
        "memories": [],
        "offset": 0,
        "limit": management_browser.MAX_LIST_LIMIT,
        "has_more": False,
        "total": 0,
        "query": "Marmalade",
    }
    memory.async_browse.assert_awaited_once_with(
        "user-a",
        "Marmalade",
        limit=management_browser.MAX_LIST_LIMIT,
        offset=0,
    )
    list_page.assert_not_awaited()
