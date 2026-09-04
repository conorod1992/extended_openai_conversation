"""Regression tests for persistence-safe Function Tool runtime configs."""

from copy import deepcopy
from typing import Any

import voluptuous as vol
import yaml

from custom_components.extended_openai_conversation_responses.agent_config import (
    configured_function_tools_from_data,
    merge_agent_config,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.functions.base import (
    Function,
    _RuntimeFunctionConfig,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm


class _RuntimeOnlyValue:
    """Stand in for HA runtime objects that must never enter persisted config."""

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        raise TypeError("runtime-only value cannot be deep-copied")


class _RuntimeConvertingFunction(Function):
    """Function whose schema converts a plain value into a runtime-only object."""

    def __init__(self) -> None:
        super().__init__(
            vol.Schema({vol.Required("value"): lambda _value: _RuntimeOnlyValue()})
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> Any:
        return None


def test_runtime_function_config_deepcopy_preserves_runtime_value() -> None:
    """Runtime copies must keep schema-created objects hydrated and copy containers."""
    raw_config = {"type": "test", "value": "{{ plain_source }}"}
    runtime_config = _RuntimeConvertingFunction().validate_schema(raw_config)
    runtime_value = runtime_config["value"]

    copied = deepcopy(runtime_config)

    assert isinstance(copied, _RuntimeFunctionConfig)
    assert copied is not runtime_config
    assert isinstance(copied["value"], _RuntimeOnlyValue)
    assert copied["value"] is runtime_value
    assert copied.persisted_copy() == raw_config


def test_runtime_function_config_is_safe_inside_runtime_tool_copy() -> None:
    """Nested runtime tool copies keep hydrated values without sharing containers."""
    raw_config = {"type": "test", "value": "{{ plain_source }}"}
    runtime_config = _RuntimeConvertingFunction().validate_schema(raw_config)
    tool = {
        "spec": {
            "name": "test_tool",
            "description": "Test tool",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": runtime_config,
    }

    copied = deepcopy({"functions": [tool]})
    copied_config = copied["functions"][0]["function"]

    assert isinstance(copied_config, _RuntimeFunctionConfig)
    assert copied_config is not runtime_config
    assert isinstance(copied_config["value"], _RuntimeOnlyValue)
    assert copied_config.persisted_copy() == raw_config


def test_runtime_function_config_yaml_uses_plain_source() -> None:
    """Persistence serialization must never traverse runtime-only objects."""
    raw_config = {"type": "test", "value": "{{ plain_source }}"}
    runtime_config = _RuntimeConvertingFunction().validate_schema(raw_config)

    persisted = yaml.safe_load(
        yaml.safe_dump({"function": runtime_config}, sort_keys=False)
    )

    assert persisted == {"function": raw_config}


async def test_agent_merge_persists_runtime_template_as_plain_yaml(hass) -> None:
    """A HA-bound Template can pass through the exact agent persistence merge."""
    tool = {
        "spec": {
            "name": "render_value",
            "description": "Render a value",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "template",
            "value_template": "{{ states('sensor.example') }}",
        },
    }
    data = {CONF_FUNCTION_TOOLS: yaml.safe_dump([tool], sort_keys=False)}
    runtime_tools = configured_function_tools_from_data(data)

    assert runtime_tools[0]["function"]["value_template"].hass is hass

    merged = merge_agent_config(data, {CONF_FUNCTION_TOOLS: runtime_tools})
    persisted_tools = yaml.safe_load(merged[CONF_FUNCTION_TOOLS])

    assert persisted_tools == [tool]


async def test_agent_merge_persists_nested_script_templates_as_plain_yaml(hass) -> None:
    """Nested templates from HA's Script schema remain persistence-safe."""
    tool = {
        "spec": {
            "name": "set_light",
            "description": "Set a templated light level",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": "light.example"},
                    "data": {"brightness_pct": "{{ brightness_pct }}"},
                }
            ],
        },
    }
    data = {CONF_FUNCTION_TOOLS: yaml.safe_dump([tool], sort_keys=False)}
    runtime_tools = configured_function_tools_from_data(data)
    runtime_brightness = runtime_tools[0]["function"]["sequence"][0]["data"][
        "brightness_pct"
    ]

    assert runtime_brightness.hass is hass

    merged = merge_agent_config(data, {CONF_FUNCTION_TOOLS: runtime_tools})
    persisted_tools = yaml.safe_load(merged[CONF_FUNCTION_TOOLS])

    assert persisted_tools == [tool]
