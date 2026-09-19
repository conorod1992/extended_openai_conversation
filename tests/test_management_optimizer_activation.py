"""Regression coverage for management optimizer activation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    _snapshot_normalized_configuration,
)


def test_management_panel_dependencies_are_build_inputs() -> None:
    """Every direct panel dependency remains present for the production build."""
    required = {
        "management-feature-status.js",
        "management-memory-settings.js",
        "memory-settings-ui.js",
        "management-capabilities-ia.js",
        "quiet-hours-ui.js",
        "voice-identity-ui.js",
        "management-permission-boundaries.js",
        "management-navigation-search.js",
    }
    frontend = Path(management_ui.__file__).parent / "frontend"
    assert all((frontend / name).is_file() for name in required)


def test_single_pass_save_snapshot_keeps_frontend_function_shape() -> None:
    """Stored YAML tools are parsed without another full config normalization."""
    snapshot = _snapshot_normalized_configuration(agent_config_defaults())
    assert isinstance(snapshot[CONF_FUNCTION_TOOLS], list)
    assert isinstance(snapshot[CONF_FUNCTION_GROUPS], list)


async def test_debug_assets_are_registered_before_management_panel(
    hass, monkeypatch
) -> None:
    """The management panel must not race its lazy Request Debug assets."""
    for name in ("apply_openai_compatibility",):
        monkeypatch.setattr(integration, name, MagicMock())

    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())

    order: list[str] = []

    async def setup_debug(_hass):
        order.append("debug")

    async def setup_management(_hass):
        order.append("management")

    monkeypatch.setattr(integration, "async_setup_debug_ui", setup_debug)
    monkeypatch.setattr(integration, "async_setup_management_ui", setup_management)

    assert await integration.async_setup(hass, {}) is True
    assert order == ["debug", "management"]


def test_native_management_lifecycle_assets_are_build_inputs() -> None:
    """Direct imports remain source inputs to the production bundle."""
    required = {
        "management-actions.js",
        "management-cache.js",
        "management-dialogs.js",
        "management-renderer.js",
        "management-route.js",
    }
    frontend = Path(management_ui.__file__).parent / "frontend"
    assert all((frontend / name).is_file() for name in required)
