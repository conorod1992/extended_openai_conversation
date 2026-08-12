"""Tests for authenticated agent configuration management operations."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from homeassistant.exceptions import HomeAssistantError


def _setup_entry(hass):
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=agent_config_defaults(),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        domain=DOMAIN,
        title="Provider",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    hass.config_entries.async_get_entry.return_value = entry
    return entry, subentry


async def test_configuration_read_and_update_require_admin(hass) -> None:
    _entry, _subentry = _setup_entry(hass)
    message = {
        "section": "configuration",
        "action": "get",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
    }
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await async_management_command(hass, "user", False, message)

    result = await async_management_command(hass, "admin", True, message)
    assert result["title"] == "Jarvis"
    assert result["defaults"]["max_tokens"] == 500
    assert result["options"]["api_mode"][0] == {"value": "auto", "label": "Auto"}

    updated = await async_management_command(
        hass,
        "admin",
        True,
        {
            **message,
            "action": "update",
            "title": "Jarvis Home",
            "config": {"max_tokens": 750},
        },
    )
    assert updated["config"]["max_tokens"] == 750
    hass.config_entries.async_update_subentry.assert_called_once()


async def test_configuration_validation_returns_field_errors(hass) -> None:
    _setup_entry(hass)
    result = await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "validate",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {
                "speech_regex_replacements": [{"pattern": "[", "replacement": ""}]
            },
        },
    )
    assert result["valid"] is False
    assert "speech_regex_replacements[0].pattern" in result["errors"]


async def test_duplicate_copies_configuration_not_runtime_history(hass) -> None:
    _entry, subentry = _setup_entry(hass)
    subentry.data["prompt"] = "Custom prompt"
    result = await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "duplicate",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )
    assert result["title"] == "Jarvis - Copy"
    duplicate = hass.config_entries.async_add_subentry.call_args.args[1]
    assert duplicate.data["prompt"] == "Custom prompt"
    assert "archive_history" not in duplicate.data
    assert "memory_contents" not in duplicate.data


async def test_function_tool_yaml_management_operations(hass) -> None:
    _setup_entry(hass)
    tool = {
        "spec": {
            "name": "test_tool",
            "description": "Test tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
        "x-extension": {"enabled": True},
    }
    base = {
        "section": "tools",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
    }
    serialized = await async_management_command(
        hass, "admin", True, {**base, "action": "serialize", "tool": tool}
    )
    assert serialized["yaml"].startswith("spec:\n")
    assert "x-extension:" in serialized["yaml"]

    validated = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "validate_yaml", "yaml": serialized["yaml"]},
    )
    assert validated["valid"] is True
    assert validated["name"] == "test_tool"
    assert validated["type"] == "native"
    assert validated["config"]["x-extension"] == {"enabled": True}

    invalid = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "validate_yaml", "yaml": "spec: ["},
    )
    assert invalid["valid"] is False
    assert "functions" in invalid["errors"]

    starter = await async_management_command(
        hass, "admin", True, {**base, "action": "starter"}
    )
    assert starter["yaml"].startswith("spec:\n")


async def test_function_tool_yaml_operations_require_admin(hass) -> None:
    _setup_entry(hass)
    with pytest.raises(HomeAssistantError, match="Administrator"):
        await async_management_command(
            hass,
            "user",
            False,
            {
                "section": "tools",
                "action": "starter",
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
            },
        )
