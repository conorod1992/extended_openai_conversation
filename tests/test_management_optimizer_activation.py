"""Regression coverage for management optimizer activation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import management_ui


def test_management_bootstrap_dependencies_are_registered() -> None:
    """Every eager bootstrap dependency must have a management static route."""
    required = {
        "management-feature-status.js",
        "management-memory-settings.js",
        "memory-settings-ui.js",
        "management-capabilities-ia.js",
        "management-voice-identity.js",
        "voice-identity-ui.js",
        "management-permission-boundaries.js",
        "management-navigation-search.js",
    }
    assert required <= set(management_ui.MANAGEMENT_FRONTEND_MODULES)


async def test_debug_assets_are_registered_before_management_panel(
    hass, monkeypatch
) -> None:
    """The panel bootstrap must not race its debug-management dependency."""
    for name in (
        "apply_openai_compatibility",
        "install_persistence_transactions",
        "install_performance_optimizations",
        "install_guest_policy_fast_path",
        "install_deferred_context_summary",
        "install_debug_instrumentation",
        "install_request_rule_match_preview",
        "install_management_loading_optimizations",
        "install_management_permissions",
        "install_safety_hardening",
    ):
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
