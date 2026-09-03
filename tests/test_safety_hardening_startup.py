"""Regression coverage for production safety-hardening startup wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import custom_components.extended_openai_conversation_responses as integration


async def test_async_setup_installs_safety_hardening(hass, monkeypatch) -> None:
    """Production setup must not leave the final safety guards as dead code."""
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
    ):
        monkeypatch.setattr(integration, name, MagicMock())

    install_safety_hardening = MagicMock()
    monkeypatch.setattr(
        integration, "install_safety_hardening", install_safety_hardening
    )

    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_management_ui",
        "async_setup_debug_ui",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())

    assert await integration.async_setup(hass, {}) is True
    install_safety_hardening.assert_called_once_with()


async def test_management_loading_is_installed_before_permissions(
    hass, monkeypatch
) -> None:
    """Keep the permission wrapper outermost around optimized management routes."""
    order: list[str] = []
    for name in (
        "apply_openai_compatibility",
        "install_persistence_transactions",
        "install_performance_optimizations",
        "install_guest_policy_fast_path",
        "install_deferred_context_summary",
        "install_debug_instrumentation",
        "install_request_rule_match_preview",
        "install_safety_hardening",
    ):
        monkeypatch.setattr(integration, name, MagicMock())

    monkeypatch.setattr(
        integration,
        "install_management_loading_optimizations",
        MagicMock(side_effect=lambda: order.append("loading")),
    )
    monkeypatch.setattr(
        integration,
        "install_management_permissions",
        MagicMock(side_effect=lambda: order.append("permissions")),
    )

    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_management_ui",
        "async_setup_debug_ui",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())

    assert await integration.async_setup(hass, {}) is True
    assert order == ["loading", "permissions"]
