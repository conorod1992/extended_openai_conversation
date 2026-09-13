"""Focused residual coverage for management authorization boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance,
    management_permissions,
)


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

    with pytest.raises(HomeAssistantError, match="Administrator permission is required"):
        await management_permissions._quiet_hours_command(
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

    assert await management_permissions._quiet_hours_command(
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
        await management_permissions._quiet_hours_command(
            SimpleNamespace(), True, {"action": "update", "config": "invalid"}
        )

    config = {"enabled": False}
    assert await management_permissions._quiet_hours_command(
        SimpleNamespace(), True, {"action": "update", "config": config}
    ) == {"enabled": False}
    assert manager.configs == [config]

    manager.fail_update = ValueError("invalid schedule")
    with pytest.raises(HomeAssistantError, match="invalid schedule") as err:
        await management_permissions._quiet_hours_command(
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
        await management_permissions._quiet_hours_command(
            SimpleNamespace(), True, {"action": "remove"}
        )


async def test_permission_wrapper_blocks_global_sections_before_dispatch() -> None:
    calls: list[dict[str, Any]] = []

    async def original(_hass, _user_id, _is_admin, message):
        calls.append(message)
        return {"ok": True}

    wrapped = management_permissions.wrap_management_permissions(original)

    for section in ("knowledge", "diagnostics"):
        with pytest.raises(HomeAssistantError, match="Administrator permission is required"):
            await wrapped(
                SimpleNamespace(), "user", False, {"section": section, "action": "get"}
            )

    assert calls == []


async def test_permission_wrapper_sanitizes_usage_and_overview_for_non_admin() -> None:
    calls: list[tuple[str, str | None, bool]] = []

    async def original(_hass, _user_id, is_admin, message):
        section = message.get("section", "overview")
        action = message.get("action")
        calls.append((section, action, is_admin))
        if section == "usage":
            return {"total": 12, "latest": {"request_id": "secret"}}
        return {
            "status": "ok",
            "usage": {
                "requests": 12,
                "latest": {"request_id": "secret", "model": "private"},
            },
        }

    wrapped = management_permissions.wrap_management_permissions(original)

    usage = await wrapped(
        SimpleNamespace(), "user", False, {"section": "usage", "action": "summary"}
    )
    assert usage == {"total": 12, "latest": None}

    overview = await wrapped(
        SimpleNamespace(), "user", False, {"section": "overview", "action": "summary"}
    )
    assert overview["status"] == "ok"
    assert overview["usage"] == {"requests": 12, "latest": None}

    with pytest.raises(HomeAssistantError, match="Administrator permission is required"):
        await wrapped(
            SimpleNamespace(), "user", False, {"section": "usage", "action": "history"}
        )

    assert calls == [
        ("usage", "summary", False),
        ("overview", "summary", False),
    ]


async def test_permission_wrapper_preserves_admin_and_safe_passthrough_results() -> None:
    result = {"usage": {"latest": {"request_id": "visible"}}}

    async def original(_hass, _user_id, _is_admin, _message):
        return result

    wrapped = management_permissions.wrap_management_permissions(original)

    assert (
        await wrapped(
            SimpleNamespace(), "admin", True, {"section": "overview", "action": "summary"}
        )
        is result
    )
    assert (
        await wrapped(SimpleNamespace(), "user", False, {"section": "configuration"})
        is result
    )


def test_sanitize_overview_leaves_non_mapping_usage_unchanged() -> None:
    result = {"status": "ok", "usage": "not-a-mapping"}
    assert management_permissions.sanitize_non_admin_overview(result) is result


async def test_optimized_overview_guard_sanitizes_non_admin_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def original(_hass, _user_id, is_admin, _message):
        calls.append(is_admin)
        return {
            "usage": {
                "requests": 3,
                "latest": {"request_id": "secret"},
            }
        }

    monkeypatch.setattr(management_loading_performance, "async_overview_summary", original)
    monkeypatch.delattr(
        management_loading_performance,
        management_permissions._OPTIMIZED_OVERVIEW_PATCHED,
        raising=False,
    )

    management_permissions._install_optimized_overview_guard()
    wrapped = management_loading_performance.async_overview_summary

    non_admin = await wrapped(SimpleNamespace(), "user", False, {})
    assert non_admin["usage"]["latest"] is None

    admin = await wrapped(SimpleNamespace(), "admin", True, {})
    assert admin["usage"]["latest"] == {"request_id": "secret"}

    management_permissions._install_optimized_overview_guard()
    assert management_loading_performance.async_overview_summary is wrapped
    assert calls == [False, True]


async def test_quiet_hours_wrapper_handles_global_section_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _QuietHoursManager()
    dispatched = False

    async def get_manager(_hass):
        return manager

    async def original(_hass, _user_id, _is_admin, _message):
        nonlocal dispatched
        dispatched = True
        return {"unexpected": True}

    monkeypatch.setattr(management_permissions, "async_get_quiet_hours", get_manager)
    wrapped = management_permissions.wrap_management_permissions(original)

    result = await wrapped(
        SimpleNamespace(), "admin", True, {"section": "quiet_hours", "action": "get"}
    )

    assert result == {"enabled": True, "periods": []}
    assert dispatched is False


def test_install_management_permissions_is_idempotent_and_keeps_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def mark(name: str):
        return lambda: calls.append(name)

    monkeypatch.setattr(management_permissions, "install_management_browser", mark("browser"))
    monkeypatch.setattr(
        management_permissions, "_install_optimized_overview_guard", mark("overview")
    )
    monkeypatch.setattr(
        management_permissions, "install_management_setup_health", mark("setup")
    )
    monkeypatch.setattr(
        management_permissions, "install_management_history_bounds", mark("history")
    )
    monkeypatch.setattr(
        management_permissions,
        "install_management_configuration_guidance",
        mark("guidance"),
    )

    async def original(_hass, _user_id, _is_admin, _message):
        return {"ok": True}

    monkeypatch.setattr(management_permissions.management_ui, "async_management_command", original)
    monkeypatch.delattr(
        management_permissions.management_ui,
        management_permissions._PATCHED,
        raising=False,
    )

    assert management_permissions.install_management_permissions() is True
    installed = management_permissions.management_ui.async_management_command
    assert installed is not original

    assert management_permissions.install_management_permissions() is False
    assert management_permissions.management_ui.async_management_command is installed
    assert calls == [
        "browser",
        "overview",
        "setup",
        "history",
        "guidance",
        "browser",
        "overview",
        "setup",
        "history",
        "guidance",
    ]
