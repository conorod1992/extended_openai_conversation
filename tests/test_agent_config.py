"""Tests for the shared conversation-agent configuration contract."""

from pathlib import Path

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    agent_config_defaults,
    agent_config_options,
    agent_config_snapshot,
    function_tool_enabled,
    function_tool_yaml,
    merge_agent_config,
    model_capabilities,
    normalize_agent_config,
    starter_function_tool_yaml,
    validate_function_tools,
    validate_single_function_tool,
)
from custom_components.extended_openai_conversation_responses.built_in_functions import (
    built_in_function_catalog,
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


def test_normalization_accepts_legacy_numeric_selector_values() -> None:
    result = normalize_agent_config(
        {
            "archive_session_timeout_minutes": "30",
            "archive_retention_days": "90",
            "usage_request_retention_days": "7",
            "max_tokens": 750.0,
            "temperature": "0.5",
        }
    )
    assert result["archive_session_timeout_minutes"] == 30
    assert result["archive_retention_days"] == 90
    assert result["usage_request_retention_days"] == 7
    assert result["max_tokens"] == 750
    assert result["temperature"] == 0.5


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


def test_function_tool_enabled_defaults_and_boolean_validation() -> None:
    legacy = validate_function_tools([_native_tool()])[0]
    assert "enabled" not in legacy
    assert function_tool_enabled(legacy) is True
    for value in (True, False):
        tool = _native_tool()
        tool["enabled"] = value
        assert validate_function_tools([tool])[0]["enabled"] is value
    for value in (0, 1, "false", None):
        tool = _native_tool()
        tool["enabled"] = value
        with pytest.raises(AgentConfigError, match=r"enabled.*boolean"):
            validate_function_tools([tool])


def test_built_in_catalogue_presets_are_valid_and_marks_used_native_tools() -> None:
    configured = _native_tool()
    catalog = built_in_function_catalog([configured])
    assert len(catalog) == 7
    assert all(validate_function_tools([preset["tool"]]) for preset in catalog)
    execute_service = next(
        preset for preset in catalog if preset["implementation"] == "execute_service"
    )
    assert execute_service["already_configured"] is True
    assert execute_service["tool"]["spec"]["name"] == "execute_service"
    assert (
        next(preset for preset in catalog if preset["implementation"] == "get_energy")[
            "already_configured"
        ]
        is False
    )


def test_function_tool_unknown_fields_are_preserved() -> None:
    tool = _native_tool()
    tool["x-extension"] = {"enabled": True}
    assert validate_function_tools([tool])[0]["x-extension"] == {"enabled": True}


def test_single_function_tool_yaml_round_trip_preserves_structure() -> None:
    tool = _native_tool()
    tool["x-extension"] = {"enabled": True, "labels": ["one", "two"]}
    serialized = function_tool_yaml(tool)
    assert serialized.startswith("spec:\n")
    assert not serialized.startswith("-")
    assert validate_single_function_tool(serialized) == tool


def test_single_function_tool_yaml_rejects_invalid_yaml_and_list_wrapper() -> None:
    with pytest.raises(AgentConfigError, match="invalid YAML"):
        validate_single_function_tool("spec: [")
    with pytest.raises(AgentConfigError, match="must contain an object"):
        validate_single_function_tool([_native_tool()])


def test_starter_function_tool_yaml_has_clean_single_tool_shape() -> None:
    starter = starter_function_tool_yaml()
    assert starter.startswith("spec:\n")
    assert "name: my_tool" in starter
    assert "type: native" in starter


@pytest.mark.parametrize(
    "fixture_name",
    [
        "native_execute_service_example.yaml",
        "template_example.yaml",
        "bash_example.yaml",
        "rest_example.yaml",
        "scrape_example.yaml",
        "sqlite_example.yaml",
        "script_example.yaml",
        "read_file_example.yaml",
        "write_file_example.yaml",
        "edit_file_example.yaml",
        "composite_example.yaml",
    ],
)
def test_single_tool_yaml_validates_existing_function_types(
    hass, fixture_name: str
) -> None:
    tools = yaml.safe_load(
        (Path(__file__).parent / "fixtures" / "functions" / fixture_name).read_text(
            encoding="utf-8"
        )
    )
    assert validate_single_function_tool(yaml.safe_dump(tools[0]))["spec"]["name"]


def test_regex_validation_is_field_specific_and_ordered() -> None:
    config = normalize_agent_config(
        {
            CONF_SPEECH_REGEX_REPLACEMENTS: [
                {"pattern": "HA", "replacement": "Home Assistant"}
            ]
        }
    )
    assert config[CONF_SPEECH_REGEX_REPLACEMENTS][0]["replacement"] == "Home Assistant"
    capture = normalize_agent_config(
        {
            CONF_SPEECH_REGEX_REPLACEMENTS: [
                {"pattern": "(HA)", "replacement": r"\1 assistant"}
            ]
        }
    )
    assert capture[CONF_SPEECH_REGEX_REPLACEMENTS][0]["replacement"] == r"\1 assistant"
    with pytest.raises(
        AgentConfigError, match=r"speech_regex_replacements\[0\]\.pattern"
    ):
        normalize_agent_config(
            {CONF_SPEECH_REGEX_REPLACEMENTS: [{"pattern": "[", "replacement": ""}]}
        )
    with pytest.raises(
        AgentConfigError, match=r"speech_regex_replacements\[0\]\.replacement"
    ):
        normalize_agent_config(
            {
                CONF_SPEECH_REGEX_REPLACEMENTS: [
                    {"pattern": "(HA)", "replacement": r"\2 assistant"}
                ]
            }
        )
    with pytest.raises(AgentConfigError, match="invalid replacement expression"):
        normalize_agent_config(
            {CONF_SPEECH_REGEX_REPLACEMENTS: [{"pattern": "HA", "replacement": "\\"}]}
        )


def test_option_metadata_is_authoritative_and_labeled() -> None:
    options = agent_config_options()
    assert {item["value"] for item in options["api_mode"]} == {
        "auto",
        "chat_completions",
        "responses",
    }
    assert all(
        set(item) == {"value", "label"}
        for choices in options.values()
        for item in choices
    )
