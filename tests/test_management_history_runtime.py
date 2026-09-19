"""Tests for bounded management history routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui as runtime,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize("section", ["settings", "overview"])
async def test_non_history_sections_retain_their_own_errors(
    hass, management_message, section
):
    with pytest.raises(
        HomeAssistantError, match=f"Unknown {section} management action: other"
    ):
        await runtime.async_management_command(
            hass, "user", True, management_message(section, "other")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"section": "usage", "action": "summary", "subentry_id": "subentry"},
        {"section": "usage", "action": "summary", "entry_id": "entry"},
    ],
)
async def test_bounded_routes_require_string_entry_and_subentry_ids(
    hass, management_message, message
):
    """History routes reject requests without both required identifiers."""
    command = runtime.async_management_command

    with pytest.raises(
        HomeAssistantError, match="entry_id and subentry_id are required"
    ):
        await command(hass, "user", True, message)


async def test_overview_keeps_partial_failure_without_second_usage_load(
    hass, management_message, monkeypatch
):
    from custom_components.extended_openai_conversation_responses import (
        management_loading_performance as loading,
    )

    get_usage = AsyncMock(side_effect=RuntimeError("usage unavailable"))
    monkeypatch.setattr(loading, "async_get_usage", get_usage)
    for name in ("async_get_memory", "async_get_knowledge", "async_get_guest_mode"):
        monkeypatch.setattr(
            loading, name, AsyncMock(side_effect=RuntimeError("unavailable"))
        )
    result = await runtime.async_management_command(
        hass, "user", True, management_message("overview", "summary")
    )
    assert result["usage"] == {}
    assert any(error["key"] == "usage" for error in result["load_errors"])
    get_usage.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "message_extra", "helper_name", "expected_call"),
    [
        ("summary", {}, "usage_summary", (("USAGE",), {})),
        (
            "daily",
            {
                "start_date": "2026-01-02",
                "end_date": "2026-02-03",
                "limit": "7",
                "offset": "4",
            },
            "usage_daily_page",
            (
                ("USAGE",),
                {
                    "start_date": "2026-01-02",
                    "end_date": "2026-02-03",
                    "limit": 7,
                    "offset": 4,
                },
            ),
        ),
        (
            "runs",
            {"limit": "8", "offset": "5", "successful": False},
            "usage_runs_page",
            (("USAGE",), {"limit": 8, "offset": 5, "successful": False}),
        ),
        (
            "breakdowns",
            {"start_date": "2026-03-01", "end_date": "2026-03-31"},
            "usage_breakdowns",
            (
                ("USAGE",),
                {"start_date": "2026-03-01", "end_date": "2026-03-31"},
            ),
        ),
    ],
)
async def test_usage_routes_to_bounded_helpers(
    hass,
    management_message,
    monkeypatch,
    action,
    message_extra,
    helper_name,
    expected_call,
):
    """Supported Usage actions call bounded helpers with normalized arguments."""
    # Selection is resolved by the management_message fixture.
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    helper = Mock(return_value={"action": action})
    monkeypatch.setattr(runtime, helper_name, helper)
    command = runtime.async_management_command
    message = {
        "section": "usage",
        "action": action,
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        **message_extra,
    }

    assert await command(hass, "user", True, message) == {"action": action}
    helper.assert_called_once_with(*expected_call[0], **expected_call[1])


@pytest.mark.asyncio
async def test_usage_daily_defaults_are_bounded(hass, management_message, monkeypatch):
    """Daily usage applies stable pagination and date defaults when omitted."""
    # Selection is resolved by the management_message fixture.
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    helper = Mock(return_value={"page": True})
    monkeypatch.setattr(runtime, "usage_daily_page", helper)
    command = runtime.async_management_command

    await command(
        hass,
        "user",
        True,
        {
            "section": "usage",
            "action": "daily",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )

    helper.assert_called_once_with(
        "USAGE",
        start_date="0000-01-01",
        end_date="9999-12-31",
        limit=366,
        offset=0,
    )


@pytest.mark.asyncio
async def test_usage_requests_requires_run_id(hass, management_message, monkeypatch):
    """Request history rejects a missing run id."""
    # Selection is resolved by the management_message fixture.
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    command = runtime.async_management_command

    with pytest.raises(HomeAssistantError, match="run_id is required"):
        await command(
            hass,
            "user",
            True,
            {
                "section": "usage",
                "action": "requests",
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
            },
        )


@pytest.mark.asyncio
async def test_usage_requests_routes_with_pagination(
    hass, management_message, monkeypatch
):
    """Request history forwards the run id and normalized pagination."""
    # Selection is resolved by the management_message fixture.
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    helper = Mock(return_value={"requests": True})
    monkeypatch.setattr(runtime, "usage_requests_page", helper)
    command = runtime.async_management_command

    result = await command(
        hass,
        "user",
        True,
        {
            "section": "usage",
            "action": "requests",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "run_id": "run-1",
            "limit": "9",
            "offset": "6",
        },
    )

    assert result == {"requests": True}
    helper.assert_called_once_with("USAGE", "run-1", limit=9, offset=6)


async def test_unknown_usage_action_has_owned_error(
    hass, management_message, monkeypatch
):
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value=object()))
    with pytest.raises(
        HomeAssistantError, match="Unknown usage management action: future-action"
    ):
        await runtime.async_management_command(
            hass, "user", True, management_message("usage", "future-action")
        )


async def test_conversation_mutations_remain_owned_by_conversation_section(
    hass, management_message, monkeypatch
):
    archive = Mock(async_delete_session=AsyncMock(return_value={"deleted": 1}))
    monkeypatch.setattr(runtime, "async_get_archive", AsyncMock(return_value=archive))
    result = await runtime.async_management_command(
        hass,
        "alice",
        False,
        management_message("conversations", "delete", session_id="session"),
    )
    assert result == {"deleted": 1}
    archive.async_delete_session.assert_awaited_once_with("user:alice", "session")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "message_extra", "helper_name", "expected_args", "expected_kwargs"),
    [
        (
            "list",
            {"limit": "7", "offset": "3"},
            "archive_list_page",
            ("ARCHIVE", "scope"),
            {"limit": 7, "offset": 3},
        ),
        (
            "search",
            {
                "query": 123,
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "limit": "8",
                "offset": "4",
            },
            "archive_search_page",
            ("ARCHIVE", "scope", "123"),
            {
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "limit": 8,
                "offset": 4,
            },
        ),
        (
            "get",
            {"session_id": 456, "start_turn": "2", "limit": "11"},
            "archive_get_page",
            ("ARCHIVE", "scope", "456"),
            {"start_turn": 2, "limit": 11},
        ),
    ],
)
async def test_conversation_routes_to_bounded_archive_helpers(
    hass,
    management_message,
    monkeypatch,
    action,
    message_extra,
    helper_name,
    expected_args,
    expected_kwargs,
):
    """Conversation history resolves scope/archive then uses the bounded helper."""
    # Selection is resolved by the management_message fixture.
    selected_scope = Mock(return_value="scope")
    monkeypatch.setattr(runtime, "_selected_scope", selected_scope)
    monkeypatch.setattr(runtime, "async_get_archive", AsyncMock(return_value="ARCHIVE"))
    helper = AsyncMock(return_value={"action": action})
    monkeypatch.setattr(runtime, helper_name, helper)
    command = runtime.async_management_command
    message = {
        "section": "conversations",
        "action": action,
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "scope_id": "requested-scope",
        **message_extra,
    }

    assert await command(hass, "user", True, message) == {"action": action}
    selected_scope.assert_called_once_with("user", True, "requested-scope")
    helper.assert_awaited_once_with(*expected_args, **expected_kwargs)
