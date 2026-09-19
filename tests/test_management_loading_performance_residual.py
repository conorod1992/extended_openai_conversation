"""Residual branch coverage for management loading/performance helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    debug_ui,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.debug_ui import (
    async_setup_debug_ui,
)
import custom_components.extended_openai_conversation_responses.management_loading_performance as loading
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_setup_management_ui,
)
from homeassistant.exceptions import HomeAssistantError


class _ConfigEntries:
    def __init__(self, entry):
        self.entry = entry

    def async_entries(self, domain):
        assert domain == DOMAIN
        return [self.entry]


def _hass_with_agent():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Provider",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    hass = SimpleNamespace(data={}, config_entries=_ConfigEntries(entry))
    return hass, entry, subentry


async def test_overview_summary_isolates_each_manager_failure(monkeypatch) -> None:
    """A failed optional manager must degrade the summary instead of failing it."""
    hass, entry, subentry = _hass_with_agent()
    fake_ui = SimpleNamespace(
        entry_and_agent=lambda *_args: (entry, subentry),
        asdict_or_none=lambda value: value,
        _settings_snapshot=lambda config: {"chat_model": config["chat_model"]},
        function_tool_enabled=lambda tool: tool.get("enabled", True) is True,
    )
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)
    monkeypatch.setattr(loading, "get_loaded_guest_mode", lambda *_args: None)

    failures = {
        "async_get_usage": RuntimeError("usage failed"),
        "async_get_memory": RuntimeError("memory failed"),
        "async_get_knowledge": RuntimeError("knowledge failed"),
        "async_get_guest_mode": RuntimeError("guest failed"),
    }
    for name, error in failures.items():
        monkeypatch.setattr(loading, name, AsyncMock(side_effect=error))

    result = await loading.async_overview_summary(
        hass,
        entry,
        subentry,
        is_admin=True,
    )

    assert result["usage"] == {}
    assert result["agent"]["memory_count"] == 0
    assert result["agent"]["knowledge_source_count"] == 0
    assert result["agent"]["tokens_today"] == 0
    assert result["agent"]["guest_mode"]["state"] == "unloaded"
    assert [error["key"] for error in result["load_errors"]] == [
        "usage",
        "memories",
        "knowledge",
        "guest_mode",
    ]
    assert [error["message"] for error in result["load_errors"]] == [
        "usage failed",
        "memory failed",
        "knowledge failed",
        "guest failed",
    ]


async def test_overview_summary_uses_exception_type_when_message_is_empty(
    monkeypatch,
) -> None:
    """Empty exception messages still produce useful load-error diagnostics."""
    hass, entry, subentry = _hass_with_agent()
    knowledge = SimpleNamespace(source_count=0)
    guest = SimpleNamespace(status=lambda: {"state": "off", "currently_active": False})
    fake_ui = SimpleNamespace(
        entry_and_agent=lambda *_args: (entry, subentry),
        asdict_or_none=lambda value: value,
        _settings_snapshot=lambda config: config,
        function_tool_enabled=lambda tool: tool.get("enabled", True) is True,
    )
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)
    monkeypatch.setattr(
        loading, "async_get_usage", AsyncMock(side_effect=RuntimeError())
    )
    monkeypatch.setattr(
        loading,
        "async_get_memory",
        AsyncMock(return_value=SimpleNamespace(stats=lambda: {})),
    )
    monkeypatch.setattr(
        loading, "async_get_knowledge", AsyncMock(return_value=knowledge)
    )
    monkeypatch.setattr(loading, "async_get_guest_mode", AsyncMock(return_value=guest))

    result = await loading.async_overview_summary(
        hass,
        entry,
        subentry,
        is_admin=True,
    )

    assert result["load_errors"] == [
        {"key": "usage", "label": "Usage", "message": "RuntimeError"}
    ]


@pytest.mark.parametrize("title", ["   ", 123])
async def test_save_configuration_rejects_bad_title_without_writing(
    hass, management_message, title
):
    result = await management_ui.async_management_command(
        hass,
        "admin",
        True,
        management_message("configuration", "save", title=title, config={}),
    )
    assert result == {"valid": False, "errors": {"title": "must not be empty"}}
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_save_configuration_rejects_non_object_config(hass, management_message):
    with pytest.raises(HomeAssistantError, match="config must be an object"):
        await management_ui.async_management_command(
            hass,
            "admin",
            True,
            management_message("configuration", "save", config=["not", "an", "object"]),
        )
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_owned_command_routes_directly_to_loading_and_save(
    hass, management_message, management_agent, monkeypatch
):
    agents = AsyncMock(return_value={"path": "agents"})
    overview = AsyncMock(return_value={"path": "overview"})
    save = AsyncMock(return_value={"path": "save"})
    monkeypatch.setattr(loading, "async_agent_catalog", agents)
    monkeypatch.setattr(loading, "async_overview_summary", overview)
    monkeypatch.setattr(management_ui, "_async_save_configuration", save)
    assert await management_ui.async_management_command(
        hass, "user", False, {"action": "agents"}
    ) == {"path": "agents"}
    assert await management_ui.async_management_command(
        hass, "user", False, management_message("overview", "summary")
    ) == {"path": "overview"}
    assert await management_ui.async_management_command(
        hass, "user", True, management_message("configuration", "save")
    ) == {"path": "save"}
    agents.assert_awaited_once_with(hass, "user", False)
    overview.assert_awaited_once_with(hass, *management_agent, is_admin=False)
    save.assert_awaited_once()
    assert save.await_args.args[0].subentry is management_agent[1]


async def test_cached_management_setup_is_noop_when_complete(monkeypatch) -> None:
    """A completed setup marker prevents duplicate registrations."""
    setup_key = "test.management.complete"
    monkeypatch.setattr(management_ui, "_UI_SETUP", setup_key)

    await async_setup_management_ui(SimpleNamespace(data={setup_key: True}))


async def test_cached_management_setup_respects_completed_step_markers(
    monkeypatch,
) -> None:
    """Retry state skips completed static/websocket steps and resumes at panel."""
    setup_key = "test.management.partial"
    static_key = f"{setup_key}.static_paths"
    websocket_key = f"{setup_key}.websocket"
    panel_key = f"{setup_key}.panel"
    static_paths = AsyncMock(side_effect=AssertionError("static paths repeated"))
    websocket_register = MagicMock(side_effect=AssertionError("websocket repeated"))
    panel_register = AsyncMock()
    hass = SimpleNamespace(
        data={static_key: True, websocket_key: True},
        http=SimpleNamespace(async_register_static_paths=static_paths),
    )
    monkeypatch.setattr(management_ui, "_UI_SETUP", setup_key)
    monkeypatch.setattr(
        management_ui.websocket_api, "async_register_command", websocket_register
    )
    monkeypatch.setattr(
        management_ui.panel_custom, "async_register_panel", panel_register
    )

    await async_setup_management_ui(hass)

    assert hass.data[panel_key] is True
    assert hass.data[setup_key] is True
    static_paths.assert_not_awaited()
    websocket_register.assert_not_called()
    panel_register.assert_awaited_once()


async def test_cached_debug_setup_is_noop_when_complete(monkeypatch) -> None:
    """A completed debug setup marker prevents duplicate registrations."""
    setup_key = "test.debug.complete"
    monkeypatch.setattr(debug_ui, "_DEBUG_UI_SETUP", setup_key)

    await async_setup_debug_ui(SimpleNamespace(data={setup_key: True}))


async def test_cached_debug_setup_respects_completed_step_markers(monkeypatch) -> None:
    """A retry can finish without repeating already completed debug steps."""
    setup_key = "test.debug.partial"
    static_key = f"{setup_key}.static_paths"
    websocket_key = f"{setup_key}.websocket"
    static_paths = AsyncMock(side_effect=AssertionError("static paths repeated"))
    websocket_register = MagicMock(side_effect=AssertionError("websocket repeated"))
    hass = SimpleNamespace(
        data={static_key: True, websocket_key: True},
        http=SimpleNamespace(async_register_static_paths=static_paths),
    )
    monkeypatch.setattr(debug_ui, "_DEBUG_UI_SETUP", setup_key)
    monkeypatch.setattr(
        debug_ui.websocket_api, "async_register_command", websocket_register
    )

    await async_setup_debug_ui(hass)

    assert hass.data[setup_key] is True
    static_paths.assert_not_awaited()
    websocket_register.assert_not_called()
