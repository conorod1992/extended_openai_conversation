"""Regression tests for native Function Tool model/runtime schema contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.built_in_functions import (
    BUILT_IN_FUNCTION_PRESETS,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.function_execution import (
    validate_function_arguments,
)
from custom_components.extended_openai_conversation_responses.functions import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.model_payload import (
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.request import (
    format_function_tools,
)
from custom_components.extended_openai_conversation_responses.safety_hardening import (
    _install_native_tool_guards,
)

_STATISTICS_PERIODS = ["5minute", "hour", "day", "week", "month", "year"]
_STATISTICS_TYPES = [
    "change",
    "last_reset",
    "max",
    "mean",
    "min",
    "state",
    "sum",
]


def _preset_tool(implementation: str) -> dict[str, Any]:
    preset = next(
        item
        for item in BUILT_IN_FUNCTION_PRESETS
        if item["implementation"] == implementation
    )
    return deepcopy(preset["tool"])


def _service_data_schema(tool: dict[str, Any]) -> dict[str, Any]:
    implementation = tool["function"]["name"]
    properties = tool["spec"]["parameters"]["properties"]
    if implementation == "execute_service_single":
        return properties["service_data"]
    return properties["list"]["items"]["properties"]["service_data"]


@pytest.mark.parametrize("implementation", ["execute_service", "execute_service_single"])
def test_service_presets_advertise_open_service_data(implementation: str) -> None:
    """Native service presets must expose their real service-specific data contract."""
    tool = _preset_tool(implementation)
    service_data = _service_data_schema(tool)

    assert tool["spec"]["strict"] is False
    assert service_data["additionalProperties"] is True
    assert "Any valid Home Assistant service data" in service_data["description"]
    assert "color_temp_kelvin" in service_data["description"]
    assert "floor_id" in service_data["description"]
    assert "label_id" in service_data["description"]


def test_service_preset_validation_preserves_service_specific_data() -> None:
    """Configured validation must retain fields that the native runtime can execute."""
    tool = _preset_tool("execute_service")
    arguments = {
        "list": [
            {
                "domain": "light",
                "service": "turn_on",
                "service_data": {
                    "entity_id": ["light.living_room"],
                    "color_temp_kelvin": 2700,
                    "brightness_pct": 60,
                },
            }
        ]
    }

    assert validate_function_arguments(tool["spec"], arguments) == arguments


async def test_native_service_forwards_service_specific_data(
    hass, exposed_entities, llm_context
) -> None:
    """The native executor must pass color and brightness data through unchanged."""
    _install_native_tool_guards()
    function = NativeFunction()
    service_data = {
        "entity_id": ["light.living_room"],
        "color_temp_kelvin": 2700,
        "brightness_pct": 60,
    }

    result = await function.execute(
        hass,
        {"name": "execute_service"},
        {
            "list": [
                {
                    "domain": "light",
                    "service": "turn_on",
                    "service_data": service_data,
                }
            ]
        },
        llm_context,
        exposed_entities,
    )

    assert result == [{"success": True}]
    assert hass.services.async_call.await_args.kwargs["service_data"] == service_data


def test_legacy_default_execute_services_is_repaired_only_for_provider() -> None:
    """Existing stock agents gain the open contract without mutating saved config."""
    saved = deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])
    original = deepcopy(saved)

    formatted = format_function_tools([saved], API_MODE_RESPONSES)[0]

    assert saved == original
    assert formatted["name"] == "execute_services"
    assert formatted["strict"] is False
    service_data = formatted["parameters"]["properties"]["list"]["items"][
        "properties"
    ]["service_data"]
    assert service_data["additionalProperties"] is True
    assert set(service_data["properties"]) == {"entity_id", "area_id"}
    assert "Any valid Home Assistant service data" in service_data["description"]
    assert "color_temp_kelvin" in service_data["description"]


def test_closed_custom_service_schema_is_not_widened() -> None:
    """An explicitly closed user-authored native service schema remains closed."""
    tool = _preset_tool("execute_service")
    tool["spec"].pop("strict")
    service_data = _service_data_schema(tool)
    service_data["additionalProperties"] = False
    service_data["description"] = "Deliberately limited service data."
    original = deepcopy(tool)

    prepared = prepare_model_function_tools([tool])[0]

    assert tool == original
    prepared_service_data = _service_data_schema(prepared)
    assert prepared_service_data["additionalProperties"] is False
    assert prepared_service_data["description"] == "Deliberately limited service data."
    assert "strict" not in prepared["spec"]


def test_statistics_preset_matches_current_home_assistant_contract() -> None:
    """The stock statistics preset exposes current recorder periods and value types."""
    tool = _preset_tool("get_statistics")
    properties = tool["spec"]["parameters"]["properties"]

    assert tool["spec"]["strict"] is False
    assert properties["period"]["enum"] == _STATISTICS_PERIODS
    assert properties["units"]["additionalProperties"] == {"type": "string"}
    assert properties["types"]["items"]["enum"] == _STATISTICS_TYPES


def _legacy_statistics_tool(periods: list[str] | None = None) -> dict[str, Any]:
    return {
        "spec": {
            "name": "get_statistics",
            "description": "Retrieve Home Assistant long-term statistics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "enum": periods
                        or ["5minute", "hour", "day", "month"],
                    },
                    "units": {"type": "object"},
                    "types": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "function": {"type": "native", "name": "get_statistics"},
    }


def test_legacy_stock_statistics_shape_is_repaired_only_for_provider() -> None:
    """Previously inserted stock statistics tools learn current HA capabilities."""
    saved = _legacy_statistics_tool()
    original = deepcopy(saved)

    prepared = prepare_model_function_tools([saved])[0]
    properties = prepared["spec"]["parameters"]["properties"]

    assert saved == original
    assert prepared["spec"]["strict"] is False
    assert properties["period"]["enum"] == _STATISTICS_PERIODS
    assert properties["units"]["additionalProperties"] == {"type": "string"}
    assert properties["types"]["items"]["enum"] == _STATISTICS_TYPES


def test_custom_statistics_period_contract_is_not_widened() -> None:
    """Provider compatibility must not overwrite an intentionally custom schema."""
    tool = _legacy_statistics_tool(["hour", "day"])
    original = deepcopy(tool)

    prepared = prepare_model_function_tools([tool])[0]

    assert tool == original
    assert prepared == original
