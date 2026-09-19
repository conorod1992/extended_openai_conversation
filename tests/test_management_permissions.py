"""Authorization and private usage projections through the owned Management API."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance as loading,
    management_ui as ui,
)
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    "section", ["knowledge", "diagnostics", "function_repair", "quiet_hours"]
)
async def test_non_admin_cannot_access_agent_global_management_sections(hass, section):
    with pytest.raises(
        HomeAssistantError, match="Administrator permission is required"
    ):
        await ui.async_management_command(
            hass, "normal-user", False, {"section": section, "action": "list"}
        )
    hass.config_entries.async_get_entry.assert_not_called()


@pytest.mark.parametrize(
    "action",
    [
        "daily",
        "runs",
        "requests",
        "breakdowns",
        "retention",
        "footprint",
        "clear_details",
    ],
)
async def test_non_admin_cannot_access_usage_details(hass, action):
    with pytest.raises(
        HomeAssistantError, match="Administrator permission is required"
    ):
        await ui.async_management_command(
            hass, "normal-user", False, {"section": "usage", "action": action}
        )
    hass.config_entries.async_get_entry.assert_not_called()


@pytest.mark.parametrize("is_admin", [True, False])
async def test_usage_summary_keeps_aggregates_and_only_exposes_admin_latest(
    hass, management_message, monkeypatch, is_admin
):
    raw = {
        "lifetime": {"total_tokens": 1234},
        "today": {"total_tokens": 42},
        "month": {"total_tokens": 900},
        "latest": {"run_id": "private-run", "source_device_id": "kitchen"},
    }
    get_usage = AsyncMock(return_value=object())
    monkeypatch.setattr(ui, "async_get_usage", get_usage)
    monkeypatch.setattr(ui, "usage_summary", lambda _: dict(raw))
    result = await ui.async_management_command(
        hass, "user", is_admin, management_message("usage", "summary")
    )
    assert result["lifetime"]["total_tokens"] == 1234
    assert result["today"]["total_tokens"] == 42
    assert result["latest"] == (raw["latest"] if is_admin else None)
    assert raw["latest"]["run_id"] == "private-run"
    get_usage.assert_awaited_once()


@pytest.mark.parametrize("is_admin", [True, False])
async def test_overview_bounds_and_sanitizes_usage_once(
    hass, management_message, monkeypatch, is_admin
):
    get_usage = AsyncMock(return_value=object())
    monkeypatch.setattr(loading, "async_get_usage", get_usage)
    monkeypatch.setattr(
        loading,
        "usage_summary",
        lambda _: {"today": {"total_tokens": 42}, "latest": {"run_id": "private"}},
    )
    monkeypatch.setattr(
        loading,
        "async_get_memory",
        AsyncMock(return_value=SimpleNamespace(stats=lambda: {"memory_count": 2})),
    )
    monkeypatch.setattr(
        loading,
        "async_get_knowledge",
        AsyncMock(return_value=SimpleNamespace(source_count=0)),
    )
    monkeypatch.setattr(
        loading,
        "async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace(status=lambda: {})),
    )
    result = await ui.async_management_command(
        hass, "user", is_admin, management_message("overview", "summary")
    )
    assert result["usage"]["today"]["total_tokens"] == 42
    assert result["usage"]["latest"] == ({"run_id": "private"} if is_admin else None)
    assert result["agent"]["memory_count"] == 2
    get_usage.assert_awaited_once()
