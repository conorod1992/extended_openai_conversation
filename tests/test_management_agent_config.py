"""Tests for authenticated agent configuration management operations."""

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_USER,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    async_get_continuity,
)
from custom_components.extended_openai_conversation_responses.function_groups import (
    reset_function_group_runtime,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    MANAGEMENT_FRONTEND_MODULES,
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.exceptions import HomeAssistantError


def test_management_frontend_routes_cover_module_imports() -> None:
    """Every local ES module import must have a registered static route."""
    frontend_dir = (
        Path(__file__).parents[1]
        / "custom_components"
        / "extended_openai_conversation_responses"
        / "frontend"
    )
    served = set(MANAGEMENT_FRONTEND_MODULES)
    assert {"agent-config-help.js", "usage-chart.js"} <= served
    for module_name in served:
        source = (frontend_dir / module_name).read_text(encoding="utf-8")
        imports = set(re.findall(r'from "\./([^"]+\.js)"', source))
        assert imports <= served, (
            f"{module_name} imports unserved modules: {imports - served}"
        )


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


async def test_backup_management_requires_admin(hass) -> None:
    _setup_entry(hass)
    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await async_management_command(
            hass,
            "user-1",
            False,
            {
                "section": "backup",
                "action": "create",
                "entry_id": "entry-1",
                "subentry_id": "agent-1",
            },
        )


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
    subentry.data["function_groups"] = [
        {
            "id": "general",
            "name": "General",
            "description": "General tools",
            "loading_mode": "always",
            "functions": [],
        }
    ]
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
    assert duplicate.data["function_groups"][0]["id"] == "general"
    assert "archive_history" not in duplicate.data
    assert "memory_contents" not in duplicate.data


async def test_ending_continuity_discards_loaded_function_groups(hass) -> None:
    """Ending model context also ends its on-demand function-group session."""
    _setup_entry(hass)
    continuity = async_get_continuity(hass, "entry-1", "agent-1")
    resolution = await continuity.async_resolve(
        CONVERSATION_CONTINUITY_USER,
        user_scope("admin", source="test"),
        None,
        None,
        30,
    )
    assert resolution.key is not None
    runtime = reset_function_group_runtime(hass, "entry-1", "agent-1")
    previous = runtime.begin(f"continuity:{resolution.key}", 30)
    previous.loaded_group_ids.add("reminders")

    result = await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "conversations",
            "action": "end_active",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "continuity_key": resolution.key,
        },
    )

    assert result == {"ended": 1}
    recreated = runtime.begin(f"continuity:{resolution.key}", 30)
    assert recreated is not previous
    assert recreated.loaded_group_ids == set()


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

    catalog = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "built_in_catalog", "tools": [tool]},
    )
    execute_service = next(
        item
        for item in catalog["functions"]
        if item["implementation"] == "execute_service"
    )
    assert execute_service["already_configured"] is True
    assert execute_service["yaml"].startswith("spec:\n")


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
