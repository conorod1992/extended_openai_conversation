"""Focused regression coverage for remaining agent-config branches."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import agent_config


def test_validate_agent_title_rejects_non_string() -> None:
    """Reject persisted titles that are not strings."""
    with pytest.raises(agent_config.AgentConfigError, match="title: must be a string"):
        agent_config.validate_agent_title(123)


def test_tools_yaml_validates_then_serializes() -> None:
    """Serialize the canonical validated tool representation."""
    assert agent_config._tools_yaml([]) == "[]\n"


def test_validate_function_tools_normalizes_reference_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose invalid Home Assistant references as field-scoped config errors."""
    monkeypatch.setattr(agent_config, "is_ha_tool", lambda _tool: True)

    def invalid_reference(_value: Any) -> Any:
        raise ValueError("invalid HA tool reference")

    monkeypatch.setattr(agent_config, "validate_reference", invalid_reference)

    with pytest.raises(
        agent_config.AgentConfigError,
        match=r"function_tools\[0\]: invalid HA tool reference",
    ):
        agent_config.validate_function_tools(
            [{"spec": {"name": "ha_ref"}, "function": {"platform": "test"}}]
        )


def test_validate_function_groups_rejects_non_list() -> None:
    """Reject malformed top-level function-group containers."""
    with pytest.raises(agent_config.AgentConfigError, match="function_groups: must be a list"):
        agent_config.validate_function_groups({}, [])


def test_validate_function_groups_reserves_loader_for_on_demand_groups() -> None:
    """Prevent a configured tool from colliding with the on-demand loader tool."""
    tools = [{"spec": {"name": agent_config.FUNCTION_GROUP_LOADER_TOOL_NAME}}]
    groups = [
        {
            "id": "lights",
            "name": "Lights",
            "description": "Lighting tools",
            "loading_mode": agent_config.FUNCTION_GROUP_LOADING_ON_DEMAND,
            "functions": [],
        }
    ]

    with pytest.raises(
        agent_config.AgentConfigError,
        match="reserved when an on-demand function group is configured",
    ):
        agent_config.validate_function_groups(groups, tools)


def test_coerce_legacy_numbers_leaves_non_integral_float_unchanged() -> None:
    """Do not silently truncate non-integral legacy numeric values."""
    config = {agent_config.CONF_MAX_TOKENS: 1.5}
    agent_config._coerce_legacy_numbers(config)
    assert config[agent_config.CONF_MAX_TOKENS] == 1.5


def test_normalize_agent_config_rejects_non_object() -> None:
    """Reject non-object persisted agent configuration payloads."""
    with pytest.raises(agent_config.AgentConfigError, match="config: must be an object"):
        agent_config.normalize_agent_config([])  # type: ignore[arg-type]
