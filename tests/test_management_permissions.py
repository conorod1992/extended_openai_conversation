"""Tests for agent-global management authorization boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.management_permissions import (
    wrap_management_permissions,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["knowledge", "diagnostics"])
async def test_non_admin_cannot_access_agent_global_management_sections(section) -> None:
    original = AsyncMock(return_value={"unexpected": True})
    wrapped = wrap_management_permissions(original)

    with pytest.raises(HomeAssistantError, match="Administrator permission is required"):
        await wrapped(None, "normal-user", False, {"section": section, "action": "list"})

    original.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["daily", "runs", "requests", "breakdowns", "retention"])
async def test_non_admin_cannot_access_usage_details(action) -> None:
    original = AsyncMock(return_value={"unexpected": True})
    wrapped = wrap_management_permissions(original)

    with pytest.raises(HomeAssistantError, match="Administrator permission is required"):
        await wrapped(None, "normal-user", False, {"section": "usage", "action": action})

    original.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_usage_summary_keeps_aggregates_but_hides_latest_run() -> None:
    original = AsyncMock(
        return_value={
            "lifetime": {"total_tokens": 1234},
            "today": {"total_tokens": 42},
            "month": {"total_tokens": 900},
            "latest": {
                "run_id": "private-run",
                "source_device_id": "kitchen-satellite",
            },
        }
    )
    wrapped = wrap_management_permissions(original)

    result = await wrapped(
        None,
        "normal-user",
        False,
        {"section": "usage", "action": "summary"},
    )

    assert result["lifetime"]["total_tokens"] == 1234
    assert result["today"]["total_tokens"] == 42
    assert result["latest"] is None
    original.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_management_requests_pass_through_unchanged() -> None:
    expected = {"source": {"id": "knowledge-1", "content": "private"}}
    original = AsyncMock(return_value=expected)
    wrapped = wrap_management_permissions(original)
    message = {"section": "knowledge", "action": "get"}

    result = await wrapped(None, "admin-user", True, message)

    assert result is expected
    original.assert_awaited_once_with(None, "admin-user", True, message)
