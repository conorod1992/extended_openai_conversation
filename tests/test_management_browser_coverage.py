"""Focused residual coverage for management browser command routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import management_browser


@pytest.mark.asyncio
async def test_wrapper_delegates_non_browser_commands() -> None:
    original = AsyncMock(return_value={"delegated": True})
    wrapped = management_browser.wrap_management_browser(original)
    hass = object()
    message = {"section": "knowledge", "action": "list"}

    result = await wrapped(hass, "user-a", False, message)

    assert result == {"delegated": True}
    original.assert_awaited_once_with(hass, "user-a", False, message)


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
async def test_wrapper_rejects_missing_or_non_string_identifiers(
    entry_id: object,
    subentry_id: object,
) -> None:
    original = AsyncMock()
    wrapped = management_browser.wrap_management_browser(original)

    with pytest.raises(
        HomeAssistantError, match="entry_id and subentry_id are required"
    ):
        await wrapped(
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

    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrapper_routes_list_through_selected_scope_and_memory(monkeypatch) -> None:
    original = AsyncMock()
    wrapped = management_browser.wrap_management_browser(original)
    hass = object()
    memory = SimpleNamespace()
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
    monkeypatch.setattr(management_browser.management_ui, "entry_and_agent", entry_and_agent)
    monkeypatch.setattr(management_browser.management_ui, "_selected_scope", selected_scope)
    monkeypatch.setattr(management_browser.management_ui, "_memory_scope", memory_scope)
    monkeypatch.setattr(management_browser, "async_get_memory", get_memory)
    monkeypatch.setattr(management_browser, "_list_page", list_page)

    result = await wrapped(hass, "admin-user", True, message)

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
    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_search_falls_back_to_list_page(monkeypatch) -> None:
    original = AsyncMock()
    wrapped = management_browser.wrap_management_browser(original)
    memory = SimpleNamespace()
    message = {
        "section": "memories",
        "action": "search",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "query": "   ",
    }

    monkeypatch.setattr(
        management_browser.management_ui,
        "entry_and_agent",
        Mock(return_value=(object(), object())),
    )
    monkeypatch.setattr(
        management_browser.management_ui,
        "_selected_scope",
        Mock(return_value="user:user-a"),
    )
    monkeypatch.setattr(
        management_browser.management_ui,
        "_memory_scope",
        Mock(return_value="user-a"),
    )
    monkeypatch.setattr(
        management_browser, "async_get_memory", AsyncMock(return_value=memory)
    )
    list_page = AsyncMock(return_value={"page": "fallback-list"})
    search_page = Mock()
    monkeypatch.setattr(management_browser, "_list_page", list_page)
    monkeypatch.setattr(management_browser, "_search_page", search_page)

    result = await wrapped(object(), "user-a", False, message)

    assert result == {"page": "fallback-list"}
    list_page.assert_awaited_once_with(
        memory,
        "user-a",
        "user:user-a",
        message,
        include_scope=False,
    )
    search_page.assert_not_called()


@pytest.mark.asyncio
async def test_search_routes_trimmed_query_with_bounded_paging(monkeypatch) -> None:
    original = AsyncMock()
    wrapped = management_browser.wrap_management_browser(original)
    memory = SimpleNamespace()
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
        management_browser.management_ui,
        "entry_and_agent",
        Mock(return_value=(object(), object())),
    )
    monkeypatch.setattr(
        management_browser.management_ui,
        "_selected_scope",
        Mock(return_value="user:user-a"),
    )
    monkeypatch.setattr(
        management_browser.management_ui,
        "_memory_scope",
        Mock(return_value="user-a"),
    )
    monkeypatch.setattr(
        management_browser, "async_get_memory", AsyncMock(return_value=memory)
    )
    search_page = Mock(return_value={"page": "search"})
    list_page = AsyncMock()
    monkeypatch.setattr(management_browser, "_search_page", search_page)
    monkeypatch.setattr(management_browser, "_list_page", list_page)

    result = await wrapped(object(), "user-a", False, message)

    assert result == {"page": "search"}
    search_page.assert_called_once_with(
        memory,
        "user-a",
        "Marmalade",
        limit=management_browser.MAX_LIST_LIMIT,
        offset=0,
        include_scope=False,
    )
    list_page.assert_not_awaited()


def test_install_management_browser_wraps_once(monkeypatch) -> None:
    async def original(_hass, _user_id, _is_admin, _message):
        return {"original": True}

    monkeypatch.setattr(management_browser.management_ui, "async_management_command", original)
    monkeypatch.delattr(
        management_browser.management_ui,
        management_browser._PATCHED,
        raising=False,
    )

    assert management_browser.install_management_browser() is True
    installed = management_browser.management_ui.async_management_command
    assert installed is not original
    assert management_browser.install_management_browser() is False
    assert management_browser.management_ui.async_management_command is installed
