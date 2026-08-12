"""Tests for the shared conversation-agent configuration contract."""

import pytest

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    agent_config_snapshot,
    merge_agent_config,
    model_capabilities,
    normalize_agent_config,
    validate_function_tools,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_AUTO_CREATE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MODE,
    CONF_SPEECH_REGEX_REPLACEMENTS,
)


def _native_tool(name: str = "test_tool") -> dict:
    return {
        "spec": {
            "name": name,
            "description": "Test tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def test_defaults_are_isolated_and_snapshot_parses_tools(hass) -> None:
    first = agent_config_defaults()
    first["voice_device_mappings"]["kitchen"] = "user:one"
    assert agent_config_defaults()["voice_device_mappings"] == {}
    snapshot = agent_config_snapshot(agent_config_defaults())
    assert isinstance(snapshot["functions"], list)
    assert snapshot["chat_model"]


def test_normalization_preserves_legacy_memory_fields() -> None:
    result = normalize_agent_config({CONF_MEMORY_MODE: "automatic"})
    assert result[CONF_MEMORY_ENABLED] is True
    assert result[CONF_MEMORY_AUTO_CREATE] is True


def test_model_capabilities_are_model_specific() -> None:
    assert model_capabilities("gpt-5-mini")["supports_reasoning_effort"] is True
    assert model_capabilities("gpt-4o")["supports_temperature"] is True


def test_invalid_values_and_unknown_updates_are_rejected() -> None:
    with pytest.raises(AgentConfigError, match="max_tokens"):
        normalize_agent_config({"max_tokens": 0})
    with pytest.raises(AgentConfigError, match="unknown fields"):
        merge_agent_config({}, {"api_key": "must-not-be-agent-data"})


def test_function_tools_validate_yaml_schema_and_duplicates() -> None:
    assert validate_function_tools([_native_tool()])[0]["spec"]["name"] == "test_tool"
    with pytest.raises(AgentConfigError, match="invalid YAML"):
        validate_function_tools("- spec: [")
    invalid_type = _native_tool()
    invalid_type["function"] = {"type": "unknown"}
    with pytest.raises(AgentConfigError, match="unrecognized function type"):
        validate_function_tools([invalid_type])
    with pytest.raises(AgentConfigError, match="duplicate tool name"):
        validate_function_tools([_native_tool(), _native_tool()])


def test_function_tool_unknown_fields_are_preserved() -> None:
    tool = _native_tool()
    tool["x-extension"] = {"enabled": True}
    assert validate_function_tools([tool])[0]["x-extension"] == {"enabled": True}


def test_regex_validation_is_field_specific_and_ordered() -> None:
    config = normalize_agent_config(
        {
            CONF_SPEECH_REGEX_REPLACEMENTS: [
                {"pattern": "HA", "replacement": "Home Assistant"}
            ]
        }
    )
    assert config[CONF_SPEECH_REGEX_REPLACEMENTS][0]["replacement"] == "Home Assistant"
    with pytest.raises(
        AgentConfigError, match=r"speech_regex_replacements\[0\]\.pattern"
    ):
        normalize_agent_config(
            {CONF_SPEECH_REGEX_REPLACEMENTS: [{"pattern": "[", "replacement": ""}]}
        )
