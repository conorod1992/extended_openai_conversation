"""Regression tests for PR15 Function Tool configuration hardening."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_ALLOWED_FUNCTION_NAMES,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.function_tool_policy import (
    RESERVED_FUNCTION_TOOL_NAMES,
)
from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.management_ui import (
    async_management_command,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
    RequestRules,
    validate_rule,
)


def _native_tool(
    name: str = "example_tool",
    *,
    implementation: str = "execute_service",
    parameters: dict | None = None,
) -> dict:
    return {
        "spec": {
            "name": name,
            "description": "Example tool",
            "parameters": parameters
            if parameters is not None
            else {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": implementation},
    }


def _function_rule(function_name: str) -> dict:
    return validate_rule(
        {
            "id": "rule-1",
            "name": "Run configured function",
            "enabled": True,
            "phrases": ["do the thing"],
            "match_type": "equals",
            "action_type": "local_action",
            "action": {
                "actions": [
                    {
                        "type": "function",
                        "function": function_name,
                        "arguments": {},
                    }
                ],
                "success_response": "Done",
                "failure_response": "Failed",
            },
            "matching_behavior": "defaults",
            "matching": DEFAULT_MATCHING,
            "order": 0,
        }
    )


def _setup_entry(hass, *, tool_name: str = "old_tool"):
    data = agent_config_defaults()
    data[CONF_FUNCTION_TOOLS] = yaml.safe_dump([_native_tool(tool_name)], sort_keys=False)
    data[CONF_FUNCTION_GROUPS] = [
        {
            "id": "group",
            "name": "Group",
            "description": "Grouped functions",
            "loading_mode": "always",
            "functions": [tool_name],
        }
    ]
    data[CONF_GUEST_ALLOWED_FUNCTION_NAMES] = [tool_name]
    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Jarvis",
        data=data,
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


class _FakeRules:
    def __init__(self, function_name: str, *, referenced: bool = True) -> None:
        self.function_name = function_name
        self.referenced = referenced
        self.renames: list[tuple[str, str]] = []

    def function_references(self, function_name: str) -> list[dict[str, str]]:
        if self.referenced and function_name == self.function_name:
            return [{"id": "rule-1", "name": "Run configured function"}]
        return []

    async def async_rename_function_reference(self, old_name: str, new_name: str) -> int:
        self.renames.append((old_name, new_name))
        if self.referenced and old_name == self.function_name:
            self.function_name = new_name
            return 1
        return 0


def test_reserved_function_tool_names_are_rejected_unconditionally() -> None:
    assert {
        "memory_search",
        "temporary_memory_add",
        "knowledge_get",
        "conversation_search",
        "load_function_group",
        "guest_mode_restrict",
        "set_continue_conversation",
    } <= RESERVED_FUNCTION_TOOL_NAMES

    for name in RESERVED_FUNCTION_TOOL_NAMES:
        with pytest.raises(AgentConfigError, match="reserved integration tool name"):
            validate_function_tools([_native_tool(name)])


def test_function_tool_name_and_spec_contract_are_validated() -> None:
    with pytest.raises(AgentConfigError, match="at most 64 characters"):
        validate_function_tools([_native_tool("not a valid function name")])

    tool = _native_tool()
    tool["spec"]["unexpected"] = True
    with pytest.raises(AgentConfigError, match="unknown fields"):
        validate_function_tools([tool])

    tool = _native_tool()
    tool["spec"]["strict"] = "yes"
    with pytest.raises(AgentConfigError, match="must be a boolean"):
        validate_function_tools([tool])


def test_function_tool_schema_rejects_unsupported_or_malformed_keywords() -> None:
    unsupported = {
        "type": "object",
        "properties": {"email": {"type": "string", "format": "email"}},
    }
    with pytest.raises(AgentConfigError, match="unsupported keyword"):
        validate_function_tools([_native_tool(parameters=unsupported)])

    malformed = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minLength": 1}},
    }
    with pytest.raises(AgentConfigError, match="requires a string schema"):
        validate_function_tools([_native_tool(parameters=malformed)])

    invalid_pattern = {
        "type": "object",
        "properties": {"value": {"type": "string", "pattern": "["}},
    }
    with pytest.raises(AgentConfigError, match="invalid pattern"):
        validate_function_tools([_native_tool(parameters=invalid_pattern)])


def test_function_tool_schema_accepts_locally_supported_nested_contract() -> None:
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 20},
                        "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    assert validate_function_tools([_native_tool(parameters=parameters)])[0]["spec"] == _native_tool(parameters=parameters)["spec"]


def test_unknown_native_implementation_is_rejected() -> None:
    with pytest.raises(AgentConfigError, match="unknown native implementation"):
        validate_function_tools(
            [_native_tool(implementation="implementation_that_does_not_exist")]
        )
    assert validate_function_tools([_native_tool(implementation="execute_service")])


def test_bash_allow_patterns_are_validated_and_case_insensitive() -> None:
    bash_tool = {
        "spec": {
            "name": "shell_tool",
            "description": "Shell tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "bash",
            "command": "echo hello",
            "allow_unsafe_shell": True,
            "allow_patterns": ["["],
        },
    }
    with pytest.raises(AgentConfigError, match="configuration is invalid for bash"):
        validate_function_tools([bash_tool])

    BashFunction()._guard_command(
        "echo hello",
        ".",
        False,
        [r"^ECHO\b"],
    )


async def test_request_rules_report_and_rename_exact_function_references() -> None:
    store = SimpleNamespace(async_save=AsyncMock())
    rules = RequestRules(store)
    rules._initialized = True
    rules._rules = [_function_rule("old_tool")]
    rules._sort_and_compile()

    assert rules.function_references("old_tool") == [
        {"id": "rule-1", "name": "Run configured function"}
    ]
    assert rules.function_references("other_tool") == []

    assert await rules.async_rename_function_reference("old_tool", "new_tool") == 1
    assert rules.function_references("old_tool") == []
    assert rules.function_references("new_tool") == [
        {"id": "rule-1", "name": "Run configured function"}
    ]
    store.async_save.assert_awaited_once()


async def test_tool_rename_updates_group_guest_and_request_rule_references(
    hass, monkeypatch
) -> None:
    entry, subentry = _setup_entry(hass)
    rules = _FakeRules("old_tool")
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        AsyncMock(return_value=rules),
    )

    result = await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "tools",
            "action": "save",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "original_name": "old_tool",
            "tool": _native_tool("new_tool"),
        },
    )

    saved = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert saved[CONF_FUNCTION_GROUPS][0]["functions"] == ["new_tool"]
    assert saved[CONF_GUEST_ALLOWED_FUNCTION_NAMES] == ["new_tool"]
    assert rules.renames == [("old_tool", "new_tool")]
    assert result["renamed_references"] == {
        "request_rules": 1,
        "guest_mode": True,
    }


async def test_tool_delete_refuses_semantic_references(hass, monkeypatch) -> None:
    entry, subentry = _setup_entry(hass)
    rules = _FakeRules("old_tool")
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        AsyncMock(return_value=rules),
    )

    with pytest.raises(HomeAssistantError, match="still referenced by"):
        await async_management_command(
            hass,
            "admin",
            True,
            {
                "section": "tools",
                "action": "delete",
                "entry_id": entry.entry_id,
                "subentry_id": subentry.subentry_id,
                "name": "old_tool",
                "confirm": True,
            },
        )
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_tool_disable_remains_allowed_and_reports_references(
    hass, monkeypatch
) -> None:
    entry, subentry = _setup_entry(hass)
    rules = _FakeRules("old_tool")
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.management_ui.async_get_request_rules",
        AsyncMock(return_value=rules),
    )

    result = await async_management_command(
        hass,
        "admin",
        True,
        {
            "section": "tools",
            "action": "set_enabled",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
            "name": "old_tool",
            "enabled": False,
        },
    )

    saved = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    saved_tools = yaml.safe_load(saved[CONF_FUNCTION_TOOLS])
    assert saved_tools[0]["enabled"] is False
    assert result["references"] == {
        "request_rules": [{"id": "rule-1", "name": "Run configured function"}],
        "guest_mode": True,
    }
