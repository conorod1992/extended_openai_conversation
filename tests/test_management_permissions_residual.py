"""Focused residual coverage for management authorization boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_permissions,
)
from homeassistant.exceptions import HomeAssistantError


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
