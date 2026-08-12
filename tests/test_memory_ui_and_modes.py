"""Tests for memory modes and the authenticated UI backend."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    normalize_agent_config,
)
from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIConversationConfigFlow,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    CONFIG_ENTRY_VERSION,
    CONTEXT_TRUNCATE_KEEP_RECENT,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_MANUAL,
    MEMORY_MODE_OFF,
)
from custom_components.extended_openai_conversation_responses.memory import (
    get_memory_mode,
)
from custom_components.extended_openai_conversation_responses.memory_ui import (
    async_manage_command,
)
from homeassistant.exceptions import HomeAssistantError


def test_legacy_memory_settings_map_to_modes() -> None:
    assert get_memory_mode({CONF_MEMORY_ENABLED: False}) == MEMORY_MODE_OFF
    assert get_memory_mode({CONF_MEMORY_ENABLED: True}) == MEMORY_MODE_MANUAL
    assert (
        get_memory_mode({CONF_MEMORY_ENABLED: True, CONF_MEMORY_AUTO_CREATE: True})
        == MEMORY_MODE_AUTOMATIC
    )


def test_mode_normalization_preserves_legacy_runtime_fields() -> None:
    normalized = normalize_agent_config(
        {
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC,
            CONF_CONTEXT_TRUNCATE_STRATEGY: CONTEXT_TRUNCATE_KEEP_RECENT,
        }
    )

    assert normalized[CONF_MEMORY_ENABLED] is True
    assert normalized[CONF_MEMORY_AUTO_CREATE] is True
    assert normalized[CONF_MEMORY_MODE] == MEMORY_MODE_AUTOMATIC


def test_config_flow_declares_current_migration_version() -> None:
    assert ExtendedOpenAIConversationConfigFlow.VERSION == CONFIG_ENTRY_VERSION


async def test_version_two_migration_preserves_memories_and_legacy_behavior() -> None:
    """Migration changes only subentry settings, never integration-owned storage."""
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        data={CONF_MEMORY_ENABLED: True, CONF_MEMORY_AUTO_CREATE: False},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        version=2,
        disabled_by=None,
        subentries={"agent-1": subentry},
    )
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [entry]

    await async_migrate_integration(hass)

    migrated = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert migrated[CONF_MEMORY_MODE] == MEMORY_MODE_MANUAL
    assert migrated[CONF_CONTEXT_TRUNCATE_STRATEGY] == "clear"
    hass.config_entries.async_update_entry.assert_called_with(
        entry, version=CONFIG_ENTRY_VERSION
    )


def _hass_and_agent():
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Family assistant",
        data={CONF_MEMORY_MODE: MEMORY_MODE_MANUAL},
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain="extended_openai_conversation_responses",
        title="OpenAI",
        subentries={"agent-1": subentry},
    )
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = entry
    hass.config_entries.async_entries.return_value = [entry]
    return hass, entry, subentry


async def test_ui_backend_memory_crud_uses_authenticated_user_scope() -> None:
    hass, _, _ = _hass_and_agent()
    record = SimpleNamespace(
        memory_id="memory-1",
        content="User prefers Celsius.",
        category="preferences",
        source="explicit",
        created_at="now",
        updated_at="now",
    )
    memory = SimpleNamespace(
        async_list=AsyncMock(return_value=[record]),
        async_add=AsyncMock(return_value={"status": "created"}),
        async_update=AsyncMock(return_value=record),
        async_delete=AsyncMock(return_value=1),
        async_clear=AsyncMock(return_value=1),
    )
    base = {"entry_id": "entry-1", "subentry_id": "agent-1"}

    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
        AsyncMock(return_value=memory),
    ):
        listed = await async_manage_command(hass, "user-7", {**base, "action": "list"})
        await async_manage_command(
            hass,
            "user-7",
            {
                **base,
                "action": "add",
                "content": record.content,
                "category": record.category,
            },
        )
        await async_manage_command(
            hass,
            "user-7",
            {
                **base,
                "action": "update",
                "memory_id": "memory-1",
                "content": "User prefers Fahrenheit.",
                "category": "preferences",
            },
        )
        await async_manage_command(
            hass,
            "user-7",
            {**base, "action": "delete", "memory_id": "memory-1"},
        )

    assert listed["memories"][0]["content"] == record.content
    memory.async_list.assert_awaited_once_with("user-7", None, 100, 0)
    memory.async_add.assert_awaited_once_with(
        "user-7", record.content, "preferences", "explicit"
    )
    memory.async_update.assert_awaited_once_with(
        "user-7", "memory-1", "User prefers Fahrenheit.", "preferences"
    )
    memory.async_delete.assert_awaited_once_with("user-7", ["memory-1"])


async def test_ui_backend_broad_clear_requires_confirmation() -> None:
    hass, _, _ = _hass_and_agent()
    memory = SimpleNamespace(async_clear=AsyncMock(return_value=2))
    message = {
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "action": "clear",
        "category": "preferences",
    }
    with patch(
        "custom_components.extended_openai_conversation_responses.memory_ui.async_get_memory",
        AsyncMock(return_value=memory),
    ):
        with pytest.raises(HomeAssistantError, match="confirmation"):
            await async_manage_command(hass, "user-7", message)
        result = await async_manage_command(
            hass, "user-7", {**message, "confirm": True}
        )

    assert result == {"deleted": 2}
    memory.async_clear.assert_awaited_once_with("user-7", "preferences")
