"""Regression coverage for production safety-hardening startup wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import custom_components.extended_openai_conversation_responses as integration
from custom_components.extended_openai_conversation_responses import (
    conversation,
    feature_status,
    guest_performance,
    management_loading_performance,
    request_static_cache,
)


async def test_async_setup_installs_safety_hardening(hass, monkeypatch) -> None:
    """Production setup must execute the real Guest installer chain successfully."""
    for name in (
        "apply_openai_compatibility",
        "install_persistence_transactions",
        "install_performance_optimizations",
        "install_deferred_context_summary",
        "install_debug_instrumentation",
        "install_request_rule_match_preview",
        "install_management_loading_optimizations",
        "install_management_permissions",
    ):
        monkeypatch.setattr(integration, name, MagicMock())

    # Keep the Guest installer itself real while isolating its existing downstream
    # installers. This catches missing internal imports in the production startup
    # chain without globally re-wrapping unrelated runtime entry points in the test.
    monkeypatch.setattr(guest_performance, "_INSTALLED", False)
    monkeypatch.setattr(
        request_static_cache, "install_request_static_caching", MagicMock()
    )
    monkeypatch.setattr(
        management_loading_performance,
        "install_management_loading_optimizations",
        MagicMock(),
    )
    monkeypatch.setattr(
        feature_status, "install_management_feature_status", MagicMock()
    )

    def base_effective_guest_policy(_self):
        return None

    monkeypatch.setattr(
        conversation.ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        base_effective_guest_policy,
    )

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
    assert guest_performance._INSTALLED is True
    assert getattr(
        conversation.ExtendedOpenAIAgentEntity._effective_guest_policy,
        "_extended_openai_guest_policy_fast_path",
        False,
    )


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
