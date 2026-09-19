"""Regression coverage for production safety-hardening startup wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import custom_components.extended_openai_conversation_responses as integration


async def test_async_setup_installs_safety_hardening(hass, monkeypatch) -> None:
    """Repeated setup preserves safety and performance method ownership."""
    for name in ("apply_openai_compatibility",):
        monkeypatch.setattr(integration, name, MagicMock())

    for name in (
        "async_migrate_integration",
        "async_setup_ha_permissions",
        "async_setup_services",
        "async_setup_intercom_services",
        "async_setup_management_ui",
        "async_setup_debug_ui",
    ):
        monkeypatch.setattr(integration, name, AsyncMock())

    from custom_components.extended_openai_conversation_responses import (
        backup,
        conversation,
        debug,
        debug_ui,
        delayed_tools,
        entity,
        intercom,
        management_ui,
        prompt,
        request_rules,
        services,
    )
    from custom_components.extended_openai_conversation_responses.functions.native import (
        NativeFunction,
    )

    owners = [
        (integration, "async_setup_management_ui"),
        (integration, "async_setup_debug_ui"),
        (management_ui, "async_setup_management_ui"),
        (debug_ui, "async_setup_debug_ui"),
        (debug.DebugTrace, "summary"),
        (delayed_tools.DelayedToolManager, "async_setup"),
        (delayed_tools.DelayedToolManager, "_async_retry_agent"),
        (request_rules.RequestRules, "function_references"),
        (request_rules.RequestRules, "async_rename_function_reference"),
        (conversation.ExtendedOpenAIAgentEntity, "_async_handle_message"),
        (conversation.ExtendedOpenAIAgentEntity, "_get_function_tools"),
        (conversation.ExtendedOpenAIAgentEntity, "_load_function_groups"),
        (conversation.ExtendedOpenAIAgentEntity, "_effective_guest_policy"),
        (conversation.ExtendedOpenAIAgentEntity, "_build_system_prompt"),
        (entity.ExtendedOpenAIBaseLLMEntity, "_async_handle_chat_log"),
        (entity.ExtendedOpenAIBaseLLMEntity, "_truncate_message_history"),
        (entity.ExtendedOpenAIBaseLLMEntity, "_transform_chat_stream"),
        (entity, "_format_tools"),
        (debug.DebugProviderRequest, "add_event"),
        (debug.DebugTrace, "as_dict"),
        (prompt, "render_effective_prompt"),
        (delayed_tools.DelayedToolManager, "_async_execute_due"),
        (NativeFunction, "execute_service"),
        (NativeFunction, "add_automation"),
        (NativeFunction, "get_history"),
        (NativeFunction, "get_statistics"),
        (intercom.IntercomManager, "async_initialize"),
        (intercom.IntercomManager, "async_set_enabled"),
        (backup, "async_create_backup"),
        (backup, "async_restore_backup"),
        (management_ui, "async_memories_command"),
        (services, "async_get_memory"),
        (services, "async_get_guest_mode"),
        (services, "async_set_function_tools_enabled"),
    ]
    methods = [getattr(owner, name) for owner, name in owners]
    assert await integration.async_setup(hass, {}) is True
    assert await integration.async_setup(hass, {}) is True
    assert [getattr(owner, name) for owner, name in owners] == methods
    assert not hasattr(integration, "install_safety_hardening")
