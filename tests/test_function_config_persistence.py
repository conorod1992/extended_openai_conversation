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
    copy_runtime_function_config,
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


def test_runtime_function_config_deepcopy_restores_plain_source() -> None:
    """Persistence copies must never traverse schema-created runtime objects."""
    raw_config = {"type": "test", "value": "{{ plain_source }}"}
    runtime_config = _RuntimeConvertingFunction().validate_schema(raw_config)

    assert isinstance(runtime_config["value"], _RuntimeOnlyValue)
    assert deepcopy(runtime_config) == raw_config


def test_runtime_function_config_is_safe_inside_persistence_copy() -> None:
    """The persistence-safe deepcopy boundary also works in the nested tool shape."""
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

    assert copied["functions"][0]["function"] == raw_config


def test_runtime_copy_preserves_hydrated_values_and_isolates_containers() -> None:
    """Runtime cache copies keep hydrated leaves without sharing mutable configs."""
    raw_config = {"type": "test", "value": "{{ plain_source }}"}
    runtime_config = _RuntimeConvertingFunction().validate_schema(raw_config)
    runtime_value = runtime_config["value"]
    source = {"functions": [{"function": runtime_config, "nested": [{"a": 1}]}]}

    copied = copy_runtime_function_config(source)
    copied_config = copied["functions"][0]["function"]

    assert isinstance(copied_config, _RuntimeFunctionConfig)
    assert copied_config is not runtime_config
    assert copied_config["value"] is runtime_value
    assert copied["functions"] is not source["functions"]
    assert copied["functions"][0]["nested"] is not source["functions"][0]["nested"]

    copied["functions"][0]["nested"][0]["a"] = 2
    assert source["functions"][0]["nested"][0]["a"] == 1
    assert deepcopy(copied_config) == raw_config


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
