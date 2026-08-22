"""Tests for authenticated agent configuration management operations."""

from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    GUEST_V2_FIELDS,
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_EXCLUDED_DOMAINS,
    CONF_GUEST_FUNCTION_POLICY,
    CONF_GUEST_POLICY_VERSION,
    CONVERSATION_CONTINUITY_USER,
    DOMAIN,
    GUEST_POLICY_VERSION,
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


async def test_guest_policy_requires_explicit_central_save(hass, monkeypatch) -> None:
    _entry, subentry = _setup_entry(hass)
    legacy = dict(subentry.data)
    for key in GUEST_V2_FIELDS:
        legacy.pop(key, None)
    subentry.data = legacy
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_guest_mode",
        AsyncMock(return_value=SimpleNamespace()),
    )

    await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "configuration",
            "action": "update",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {"max_tokens": 700},
        },
    )
    generic_data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert CONF_GUEST_POLICY_VERSION not in generic_data

    hass.config_entries.async_update_subentry.reset_mock()
    await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "guest_mode",
            "action": "save_policy",
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
            "config": {
                CONF_GUEST_EXCLUDED_DOMAINS: ["lock"],
                CONF_GUEST_FUNCTION_POLICY: "off",
            },
        },
    )
    saved = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert saved[CONF_GUEST_POLICY_VERSION] == GUEST_POLICY_VERSION
    assert saved[CONF_GUEST_EXCLUDED_DOMAINS] == ["lock"]


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


async def test_function_mutations_patch_latest_persisted_fields_only(hass) -> None:
    entry, subentry = _setup_entry(hass)
    original = {
        "spec": {
            "name": "original",
            "description": "Original tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }
    subentry.data.update(
        {
            "prompt": "latest persisted prompt",
            "max_tokens": 901,
            "runtime_only": {"preserved": True},
            CONF_FUNCTION_TOOLS: yaml.safe_dump([original], sort_keys=False),
            CONF_FUNCTION_GROUPS: [
                {
                    "id": "originals",
                    "name": "Originals",
                    "description": "Original tools",
                    "loading_mode": "always",
                    "functions": ["original"],
                }
            ],
        }
    )
    base = {
        "section": "tools",
        "entry_id": entry.entry_id,
        "subentry_id": subentry.subentry_id,
    }

    renamed = {
        **original,
        "spec": {**original["spec"], "name": "renamed"},
    }
    result = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "save", "tool": renamed, "original_name": "original"},
    )
    saved = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert saved["prompt"] == "latest persisted prompt"
    assert saved["max_tokens"] == 901
    assert saved["runtime_only"] == {"preserved": True}
    assert result["functions"][0]["spec"]["name"] == "renamed"
    assert result["function_groups"][0]["functions"] == ["renamed"]

    subentry.data = saved
    created_tool = {
        **original,
        "spec": {**original["spec"], "name": "created"},
    }
    created = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "save", "tool": created_tool},
    )
    assert [tool["spec"]["name"] for tool in created["functions"]] == [
        "renamed",
        "created",
    ]

    subentry.data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    hass.config_entries.async_update_subentry.reset_mock()
    toggled = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "set_enabled", "name": "renamed", "enabled": False},
    )
    assert toggled["functions"][0]["enabled"] is False

    subentry.data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    hass.config_entries.async_update_subentry.reset_mock()
    grouped = await async_management_command(
        hass,
        "admin",
        True,
        {
            **base,
            "action": "save_group",
            "group": {
                "id": "updated",
                "name": "Updated",
                "description": "Updated group",
                "loading_mode": "on_demand",
                "functions": ["renamed"],
            },
            "original_id": "originals",
        },
    )
    assert grouped["function_groups"][0]["id"] == "updated"

    subentry.data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    created_group = await async_management_command(
        hass,
        "admin",
        True,
        {
            **base,
            "action": "save_group",
            "group": {
                "id": "created",
                "name": "Created",
                "description": "Created group",
                "loading_mode": "always",
                "functions": ["created"],
            },
        },
    )
    assert [group["id"] for group in created_group["function_groups"]] == [
        "updated",
        "created",
    ]

    subentry.data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    hass.config_entries.async_update_subentry.reset_mock()
    ungrouped = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "delete_group", "group_id": "updated", "confirm": True},
    )
    assert [group["id"] for group in ungrouped["function_groups"]] == ["created"]
    assert ungrouped["functions"][0]["spec"]["name"] == "renamed"

    subentry.data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    deleted = await async_management_command(
        hass,
        "admin",
        True,
        {**base, "action": "delete", "name": "renamed", "confirm": True},
    )
    assert [tool["spec"]["name"] for tool in deleted["functions"]] == ["created"]
    assert [group["id"] for group in deleted["function_groups"]] == ["created"]


async def test_invalid_direct_function_mutation_persists_nothing(hass) -> None:
    entry, subentry = _setup_entry(hass)
    with pytest.raises(HomeAssistantError):
        await async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "tools",
                "action": "save",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "tool": {"spec": {"name": "invalid"}},
            },
        )
    hass.config_entries.async_update_subentry.assert_not_called()


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
