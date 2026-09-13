"""Tests for bounded management history routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    management_history_runtime as runtime,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"section": "settings", "action": "get"}, {"route": "settings"}),
        ({"section": "overview", "action": "other"}, {"route": "overview"}),
    ],
)
async def test_unhandled_routes_delegate_to_original(message, expected):
    """Routes outside retained-history handling remain owned by the original command."""
    original = AsyncMock(return_value=expected)
    wrapped = runtime.wrap_management_history_bounds(original)
    hass = object()

    assert await wrapped(hass, "user", False, message) == expected
    original.assert_awaited_once_with(hass, "user", False, message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        {"section": "usage", "action": "summary", "subentry_id": "subentry"},
        {"section": "usage", "action": "summary", "entry_id": "entry"},
    ],
)
async def test_bounded_routes_require_string_entry_and_subentry_ids(message):
    """History routes reject requests without both required identifiers."""
    wrapped = runtime.wrap_management_history_bounds(AsyncMock())

    with pytest.raises(
        HomeAssistantError, match="entry_id and subentry_id are required"
    ):
        await wrapped(object(), "user", False, message)


@pytest.mark.asyncio
@pytest.mark.parametrize("original_usage", [None, {}])
async def test_overview_preserves_original_when_usage_is_absent(
    monkeypatch, original_usage
):
    """Overview does not reload Usage when the optimized original path omitted it."""
    original = AsyncMock(return_value={"usage": original_usage, "other": "kept"})
    entry_and_agent = Mock()
    get_usage = AsyncMock()
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", entry_and_agent)
    monkeypatch.setattr(runtime, "async_get_usage", get_usage)
    wrapped = runtime.wrap_management_history_bounds(original)
    message = {
        "section": "overview",
        "action": "summary",
        "entry_id": "entry",
        "subentry_id": "subentry",
    }

    result = await wrapped(object(), "user", True, message)

    assert result == {"usage": original_usage, "other": "kept"}
    get_usage.assert_not_awaited()
    entry_and_agent.assert_called_once()


@pytest.mark.asyncio
async def test_overview_reprojects_successful_usage(monkeypatch):
    """Overview replaces the unbounded Usage payload with its bounded summary."""
    original = AsyncMock(return_value={"usage": {"raw": True}, "other": "kept"})
    usage = object()
    get_usage = AsyncMock(return_value=usage)
    summary = Mock(return_value={"bounded": True})
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    monkeypatch.setattr(runtime, "async_get_usage", get_usage)
    monkeypatch.setattr(runtime, "usage_summary", summary)
    wrapped = runtime.wrap_management_history_bounds(original)

    result = await wrapped(
        object(),
        "user",
        True,
        {
            "section": "overview",
            "action": "summary",
            "entry_id": "entry",
            "subentry_id": "subentry",
        },
    )

    assert result == {"usage": {"bounded": True}, "other": "kept"}
    get_usage.assert_awaited_once()
    summary.assert_called_once_with(usage)


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
    monkeypatch, action, message_extra, helper_name, expected_call
):
    """Supported Usage actions call bounded helpers with normalized arguments."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    helper = Mock(return_value={"action": action})
    monkeypatch.setattr(runtime, helper_name, helper)
    original = AsyncMock()
    wrapped = runtime.wrap_management_history_bounds(original)
    message = {
        "section": "usage",
        "action": action,
        "entry_id": "entry",
        "subentry_id": "subentry",
        **message_extra,
    }

    assert await wrapped(object(), "user", False, message) == {"action": action}
    helper.assert_called_once_with(*expected_call[0], **expected_call[1])
    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_usage_daily_defaults_are_bounded(monkeypatch):
    """Daily usage applies stable pagination and date defaults when omitted."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    helper = Mock(return_value={"page": True})
    monkeypatch.setattr(runtime, "usage_daily_page", helper)
    wrapped = runtime.wrap_management_history_bounds(AsyncMock())

    await wrapped(
        object(),
        "user",
        False,
        {
            "section": "usage",
            "action": "daily",
            "entry_id": "entry",
            "subentry_id": "subentry",
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
async def test_usage_requests_requires_run_id(monkeypatch):
    """Request history rejects a missing run id."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    wrapped = runtime.wrap_management_history_bounds(AsyncMock())

    with pytest.raises(HomeAssistantError, match="run_id is required"):
        await wrapped(
            object(),
            "user",
            False,
            {
                "section": "usage",
                "action": "requests",
                "entry_id": "entry",
                "subentry_id": "subentry",
            },
        )


@pytest.mark.asyncio
async def test_usage_requests_routes_with_pagination(monkeypatch):
    """Request history forwards the run id and normalized pagination."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    helper = Mock(return_value={"requests": True})
    monkeypatch.setattr(runtime, "usage_requests_page", helper)
    wrapped = runtime.wrap_management_history_bounds(AsyncMock())

    result = await wrapped(
        object(),
        "user",
        False,
        {
            "section": "usage",
            "action": "requests",
            "entry_id": "entry",
            "subentry_id": "subentry",
            "run_id": "run-1",
            "limit": "9",
            "offset": "6",
        },
    )

    assert result == {"requests": True}
    helper.assert_called_once_with("USAGE", "run-1", limit=9, offset=6)


@pytest.mark.asyncio
async def test_unknown_usage_action_delegates_after_validation(monkeypatch):
    """Unknown Usage actions remain delegated after history validation/load."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    monkeypatch.setattr(runtime, "async_get_usage", AsyncMock(return_value="USAGE"))
    original = AsyncMock(return_value={"delegated": True})
    wrapped = runtime.wrap_management_history_bounds(original)
    message = {
        "section": "usage",
        "action": "future-action",
        "entry_id": "entry",
        "subentry_id": "subentry",
    }

    assert await wrapped(object(), "user", False, message) == {"delegated": True}
    original.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_conversation_action_delegates_without_archive(monkeypatch):
    """Unsupported conversation actions stay with the original handler."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    get_archive = AsyncMock()
    monkeypatch.setattr(runtime, "async_get_archive", get_archive)
    original = AsyncMock(return_value={"delegated": True})
    wrapped = runtime.wrap_management_history_bounds(original)
    message = {
        "section": "conversations",
        "action": "delete",
        "entry_id": "entry",
        "subentry_id": "subentry",
    }

    assert await wrapped(object(), "user", False, message) == {"delegated": True}
    get_archive.assert_not_awaited()


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
    monkeypatch,
    action,
    message_extra,
    helper_name,
    expected_args,
    expected_kwargs,
):
    """Conversation history resolves scope/archive then uses the bounded helper."""
    monkeypatch.setattr(runtime.management_ui, "entry_and_agent", Mock())
    selected_scope = Mock(return_value="scope")
    monkeypatch.setattr(runtime.management_ui, "_selected_scope", selected_scope)
    monkeypatch.setattr(runtime, "async_get_archive", AsyncMock(return_value="ARCHIVE"))
    helper = AsyncMock(return_value={"action": action})
    monkeypatch.setattr(runtime, helper_name, helper)
    original = AsyncMock()
    wrapped = runtime.wrap_management_history_bounds(original)
    message = {
        "section": "conversations",
        "action": action,
        "entry_id": "entry",
        "subentry_id": "subentry",
        "scope_id": "requested-scope",
        **message_extra,
    }

    assert await wrapped(object(), "user", True, message) == {"action": action}
    selected_scope.assert_called_once_with("user", True, "requested-scope")
    helper.assert_awaited_once_with(*expected_args, **expected_kwargs)
    original.assert_not_awaited()


def test_register_frontend_module_appends_once(monkeypatch):
    """The pagination frontend asset is appended without duplicates."""
    monkeypatch.setattr(runtime.management_ui, "MANAGEMENT_FRONTEND_MODULES", ("base.js",))

    runtime._register_frontend_module()
    runtime._register_frontend_module()

    assert runtime.management_ui.MANAGEMENT_FRONTEND_MODULES == (
        "base.js",
        "management-history-pagination.js",
    )


def test_install_management_history_bounds_is_idempotent(monkeypatch):
    """Installation wraps once while always exposing the pagination asset."""
    original = AsyncMock()
    monkeypatch.setattr(runtime.management_ui, "async_management_command", original)
    monkeypatch.setattr(runtime.management_ui, "MANAGEMENT_FRONTEND_MODULES", ())
    monkeypatch.delattr(runtime.management_ui, runtime._PATCHED, raising=False)

    assert runtime.install_management_history_bounds() is True
    installed = runtime.management_ui.async_management_command
    assert installed is not original
    assert getattr(runtime.management_ui, runtime._PATCHED) is True
    assert runtime.management_ui.MANAGEMENT_FRONTEND_MODULES == (
        "management-history-pagination.js",
    )

    assert runtime.install_management_history_bounds() is False
    assert runtime.management_ui.async_management_command is installed
    assert runtime.management_ui.MANAGEMENT_FRONTEND_MODULES == (
        "management-history-pagination.js",
    )
