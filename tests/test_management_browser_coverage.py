"""Focused residual coverage for management browser command routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_browser,
    management_ui,
)
from homeassistant.exceptions import HomeAssistantError


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
