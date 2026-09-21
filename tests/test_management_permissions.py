"""Authorization and private usage projections through the owned Management API."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance as loading,
    management_permissions,
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
        AsyncMock(return_value=SimpleNamespace(memory_count=2)),
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


class _QuietHoursManager:
    def __init__(self) -> None:
        self.configs: list[dict[str, Any]] = []
        self.fail_update: ValueError | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": True, "periods": []}

    async def async_update_config(self, config: dict[str, Any]) -> dict[str, Any]:
        if self.fail_update is not None:
            raise self.fail_update
        self.configs.append(config)
        return {"enabled": config.get("enabled", False)}


async def test_quiet_hours_requires_admin_before_manager_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def get_manager(_hass):
        nonlocal called
        called = True
        return _QuietHoursManager()

    monkeypatch.setattr(management_permissions, "async_get_quiet_hours", get_manager)

    with pytest.raises(
        HomeAssistantError, match="Administrator permission is required"
    ):
        await management_permissions.async_quiet_hours_command(
            SimpleNamespace(), False, {"action": "get"}
        )

    assert called is False


@pytest.mark.parametrize("action", ["get", "discover"])
async def test_quiet_hours_read_actions_return_manager_snapshot(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    manager = _QuietHoursManager()

    async def get_manager(_hass):
        return manager

    monkeypatch.setattr(management_permissions, "async_get_quiet_hours", get_manager)

    assert await management_permissions.async_quiet_hours_command(
        SimpleNamespace(), True, {"action": action}
    ) == {"enabled": True, "periods": []}


async def test_quiet_hours_update_validates_config_and_translates_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _QuietHoursManager()

    async def get_manager(_hass):
        return manager

    monkeypatch.setattr(management_permissions, "async_get_quiet_hours", get_manager)

    with pytest.raises(HomeAssistantError, match="config must be an object"):
        await management_permissions.async_quiet_hours_command(
            SimpleNamespace(), True, {"action": "update", "config": "invalid"}
        )

    config = {"enabled": False}
    assert await management_permissions.async_quiet_hours_command(
        SimpleNamespace(), True, {"action": "update", "config": config}
    ) == {"enabled": False}
    assert manager.configs == [config]

    manager.fail_update = ValueError("invalid schedule")
    with pytest.raises(HomeAssistantError, match="invalid schedule") as err:
        await management_permissions.async_quiet_hours_command(
            SimpleNamespace(), True, {"action": "update", "config": {}}
        )
    assert isinstance(err.value.__cause__, ValueError)


async def test_quiet_hours_rejects_unknown_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_manager(_hass):
        return _QuietHoursManager()

    monkeypatch.setattr(management_permissions, "async_get_quiet_hours", get_manager)

    with pytest.raises(HomeAssistantError, match="Unknown Quiet Hours action: remove"):
        await management_permissions.async_quiet_hours_command(
            SimpleNamespace(), True, {"action": "remove"}
        )


async def test_quiet_hours_global_command_needs_no_agent_selection(hass, monkeypatch):
    from custom_components.extended_openai_conversation_responses import management_ui

    manager = SimpleNamespace(snapshot=lambda: {"enabled": True})
    monkeypatch.setattr(
        management_permissions, "async_get_quiet_hours", AsyncMock(return_value=manager)
    )
    result = await management_ui.async_management_command(
        hass, "admin", True, {"section": "quiet_hours", "action": "get"}
    )
    assert result == {"enabled": True}
    hass.config_entries.async_get_entry.assert_not_called()
