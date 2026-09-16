"""Residual branch coverage for management loading/performance helpers."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
import custom_components.extended_openai_conversation_responses.management_loading_performance as loading


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
        "admin",
        True,
        {"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id},
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
    monkeypatch.setattr(loading, "async_get_usage", AsyncMock(side_effect=RuntimeError()))
    monkeypatch.setattr(
        loading,
        "async_get_memory",
        AsyncMock(return_value=SimpleNamespace(stats=lambda: {})),
    )
    monkeypatch.setattr(loading, "async_get_knowledge", AsyncMock(return_value=knowledge))
    monkeypatch.setattr(loading, "async_get_guest_mode", AsyncMock(return_value=guest))

    result = await loading.async_overview_summary(
        hass,
        "admin",
        True,
        {"entry_id": entry.entry_id, "subentry_id": subentry.subentry_id},
    )

    assert result["load_errors"] == [
        {"key": "usage", "label": "Usage", "message": "RuntimeError"}
    ]


async def test_save_configuration_rejects_bad_title_before_management_lookup() -> None:
    """Blank and non-string titles are frontend validation errors, not writes."""
    hass = SimpleNamespace()

    for title in ("   ", 123):
        result = await loading._async_save_configuration(
            hass,
            "admin",
            True,
            {"title": title, "config": {}},
        )
        assert result == {"valid": False, "errors": {"title": "must not be empty"}}


async def test_save_configuration_rejects_non_object_config(monkeypatch) -> None:
    """The optimized save path keeps the original object-only API contract."""
    fake_ui = SimpleNamespace(_require_admin=MagicMock())
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)

    with pytest.raises(HomeAssistantError, match="config must be an object"):
        await loading._async_save_configuration(
            SimpleNamespace(),
            "admin",
            True,
            {"config": ["not", "an", "object"]},
        )

    fake_ui._require_admin.assert_called_once_with(True)


async def test_optimized_management_command_routes_all_fast_paths(monkeypatch) -> None:
    """Each optimized command is dispatched without touching the legacy handler."""
    legacy = AsyncMock(side_effect=AssertionError("legacy handler should not run"))
    monkeypatch.setattr(loading, "_ORIGINAL_MANAGEMENT_COMMAND", legacy)

    agents = AsyncMock(return_value={"path": "agents"})
    overview = AsyncMock(return_value={"path": "overview"})
    save = AsyncMock(return_value={"path": "save"})
    monkeypatch.setattr(loading, "async_agent_catalog", agents)
    monkeypatch.setattr(loading, "async_overview_summary", overview)
    monkeypatch.setattr(loading, "_async_save_configuration", save)

    hass = SimpleNamespace()
    assert await loading.optimized_management_command(
        hass, "user", False, {"action": "agents"}
    ) == {"path": "agents"}
    assert await loading.optimized_management_command(
        hass, "user", False, {"section": "overview", "action": "summary"}
    ) == {"path": "overview"}
    assert await loading.optimized_management_command(
        hass, "user", True, {"section": "configuration", "action": "save"}
    ) == {"path": "save"}

    legacy.assert_not_awaited()
    agents.assert_awaited_once()
    overview.assert_awaited_once()
    save.assert_awaited_once()


async def test_optimized_management_command_falls_back_to_live_handler(monkeypatch) -> None:
    """Before installation, non-optimized commands use the current management handler."""
    fallback = AsyncMock(return_value={"path": "legacy"})
    fake_ui = SimpleNamespace(async_management_command=fallback)
    monkeypatch.setattr(loading, "_ORIGINAL_MANAGEMENT_COMMAND", None)
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)

    message = {"section": "usage", "action": "summary"}
    result = await loading.optimized_management_command(
        SimpleNamespace(), "user", False, message
    )

    assert result == {"path": "legacy"}
    fallback.assert_awaited_once_with(ANY, "user", False, message)


async def test_cached_management_setup_is_noop_when_complete(monkeypatch) -> None:
    """A completed setup marker prevents duplicate registrations."""
    setup_key = "test.management.complete"
    fake_ui = SimpleNamespace(_UI_SETUP=setup_key)
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)

    await loading.async_setup_cached_management_ui(
        SimpleNamespace(data={setup_key: True})
    )


async def test_cached_management_setup_respects_completed_step_markers(
    monkeypatch,
) -> None:
    """Retry state skips completed static/websocket steps and resumes at panel."""
    setup_key = "test.management.partial"
    static_key = loading._setup_step_key(setup_key, "static_paths")
    websocket_key = loading._setup_step_key(setup_key, "websocket")
    panel_key = loading._setup_step_key(setup_key, "panel")
    fake_ui = SimpleNamespace(
        _UI_SETUP=setup_key,
        __file__="/integration/management_ui.py",
        MANAGEMENT_FRONTEND_MODULES=("management-panel.js",),
        websocket_management=object(),
    )
    static_paths = AsyncMock(side_effect=AssertionError("static paths repeated"))
    websocket_register = MagicMock(side_effect=AssertionError("websocket repeated"))
    panel_register = AsyncMock()
    hass = SimpleNamespace(
        data={static_key: True, websocket_key: True},
        http=SimpleNamespace(async_register_static_paths=static_paths),
    )
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_ui)
    monkeypatch.setattr(loading.websocket_api, "async_register_command", websocket_register)
    monkeypatch.setattr(loading.panel_custom, "async_register_panel", panel_register)

    await loading.async_setup_cached_management_ui(hass)

    assert hass.data[panel_key] is True
    assert hass.data[setup_key] is True
    static_paths.assert_not_awaited()
    websocket_register.assert_not_called()
    panel_register.assert_awaited_once()


async def test_cached_debug_setup_is_noop_when_complete(monkeypatch) -> None:
    """A completed debug setup marker prevents duplicate registrations."""
    setup_key = "test.debug.complete"
    fake_ui = SimpleNamespace(_DEBUG_UI_SETUP=setup_key)
    monkeypatch.setattr(loading, "_debug_ui", lambda: fake_ui)

    await loading.async_setup_cached_debug_ui(SimpleNamespace(data={setup_key: True}))


async def test_cached_debug_setup_respects_completed_step_markers(monkeypatch) -> None:
    """A retry can finish without repeating already completed debug steps."""
    setup_key = "test.debug.partial"
    static_key = loading._setup_step_key(setup_key, "static_paths")
    websocket_key = loading._setup_step_key(setup_key, "websocket")
    fake_ui = SimpleNamespace(
        _DEBUG_UI_SETUP=setup_key,
        __file__="/integration/debug_ui.py",
        websocket_request_debug=object(),
    )
    static_paths = AsyncMock(side_effect=AssertionError("static paths repeated"))
    websocket_register = MagicMock(side_effect=AssertionError("websocket repeated"))
    hass = SimpleNamespace(
        data={static_key: True, websocket_key: True},
        http=SimpleNamespace(async_register_static_paths=static_paths),
    )
    monkeypatch.setattr(loading, "_debug_ui", lambda: fake_ui)
    monkeypatch.setattr(loading.websocket_api, "async_register_command", websocket_register)

    await loading.async_setup_cached_debug_ui(hass)

    assert hass.data[setup_key] is True
    static_paths.assert_not_awaited()
    websocket_register.assert_not_called()


def test_install_management_loading_optimizations_is_idempotent(monkeypatch) -> None:
    """Installation patches once, deduplicates modules, and updates package exports."""
    from custom_components.extended_openai_conversation_responses import (
        conversation,
        function_tool_resolution,
    )

    original = AsyncMock()
    fake_management = SimpleNamespace(
        async_management_command=original,
        MANAGEMENT_FRONTEND_MODULES=(
            "management-panel.js",
            loading._EXTRA_FRONTEND_MODULES[0],
        ),
        async_setup_management_ui=object(),
    )
    fake_debug = SimpleNamespace(async_setup_debug_ui=object())
    fake_package = SimpleNamespace(
        conversation=conversation,
        function_tool_resolution=function_tool_resolution,
    )

    monkeypatch.setattr(loading, "_INSTALLED", False)
    monkeypatch.setattr(loading, "_ORIGINAL_MANAGEMENT_COMMAND", None)
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_management)
    monkeypatch.setattr(loading, "_debug_ui", lambda: fake_debug)
    monkeypatch.setattr(
        conversation,
        "configured_function_tools_from_data",
        conversation.configured_function_tools_from_data,
    )
    monkeypatch.setattr(
        conversation,
        "validate_function_groups",
        conversation.validate_function_groups,
    )
    monkeypatch.setattr(
        function_tool_resolution,
        "validate_function_groups",
        function_tool_resolution.validate_function_groups,
    )
    monkeypatch.setitem(loading.sys.modules, loading.__package__, fake_package)

    loading.install_management_loading_optimizations()

    assert loading._INSTALLED is True
    assert loading._ORIGINAL_MANAGEMENT_COMMAND is original
    assert fake_management.async_management_command is loading.optimized_management_command
    assert fake_management.async_setup_management_ui is loading.async_setup_cached_management_ui
    assert fake_debug.async_setup_debug_ui is loading.async_setup_cached_debug_ui
    assert conversation.configured_function_tools_from_data is loading._runtime_configured_function_tools
    assert conversation.validate_function_groups is loading._runtime_validate_function_groups
    assert function_tool_resolution.validate_function_groups is loading._runtime_validate_function_groups
    assert fake_management.MANAGEMENT_FRONTEND_MODULES.count(
        loading._EXTRA_FRONTEND_MODULES[0]
    ) == 1
    assert fake_package.async_setup_management_ui is loading.async_setup_cached_management_ui
    assert fake_package.async_setup_debug_ui is loading.async_setup_cached_debug_ui

    modules = fake_management.MANAGEMENT_FRONTEND_MODULES
    loading.install_management_loading_optimizations()
    assert fake_management.MANAGEMENT_FRONTEND_MODULES == modules


def test_install_management_loading_optimizations_without_loaded_package(
    monkeypatch,
) -> None:
    """Installation remains valid while the package module is absent."""
    from custom_components.extended_openai_conversation_responses import (
        conversation,
        function_tool_resolution,
    )

    fake_management = SimpleNamespace(
        async_management_command=AsyncMock(),
        MANAGEMENT_FRONTEND_MODULES=(),
        async_setup_management_ui=object(),
    )
    fake_debug = SimpleNamespace(async_setup_debug_ui=object())
    monkeypatch.setattr(loading, "_INSTALLED", False)
    monkeypatch.setattr(loading, "_ORIGINAL_MANAGEMENT_COMMAND", None)
    monkeypatch.setattr(loading, "_management_ui", lambda: fake_management)
    monkeypatch.setattr(loading, "_debug_ui", lambda: fake_debug)
    monkeypatch.setattr(
        conversation,
        "configured_function_tools_from_data",
        conversation.configured_function_tools_from_data,
    )
    monkeypatch.setattr(
        conversation,
        "validate_function_groups",
        conversation.validate_function_groups,
    )
    monkeypatch.setattr(
        function_tool_resolution,
        "validate_function_groups",
        function_tool_resolution.validate_function_groups,
    )
    monkeypatch.delitem(loading.sys.modules, loading.__package__, raising=False)

    loading.install_management_loading_optimizations()

    assert fake_management.async_management_command is loading.optimized_management_command
