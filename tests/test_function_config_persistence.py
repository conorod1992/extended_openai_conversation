"""Regression tests for persistence-safe Function Tool runtime configs."""

from copy import deepcopy
from typing import Any

import voluptuous as vol

from custom_components.extended_openai_conversation_responses.functions.base import Function
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
    """Persistence copies must not recurse into schema-created runtime objects."""
    raw_config = {"type": "test", "value": "{{ plain_source }}"}
    runtime_config = _RuntimeConvertingFunction().validate_schema(raw_config)

    assert isinstance(runtime_config["value"], _RuntimeOnlyValue)
    assert deepcopy(runtime_config) == raw_config


def test_runtime_function_config_is_safe_inside_tool_copy() -> None:
    """Copying a configured tool for agent persistence keeps plain function data."""
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
