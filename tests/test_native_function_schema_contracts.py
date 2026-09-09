"""Regression tests for native Function Tool model/runtime schema contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.built_in_functions import (
    BUILT_IN_FUNCTION_PRESETS,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_FUNCTION_TOOLS,
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
from custom_components.extended_openai_conversation_responses.native_function_schema_migration import (
    migrate_legacy_stock_native_function_tools,
    migrate_legacy_stock_native_function_tools_yaml,
)
from custom_components.extended_openai_conversation_responses.request import (
    format_function_tools,
)
from custom_components.extended_openai_conversation_responses.resource_limits import (
    MAX_NATIVE_SERVICE_ACTIONS,
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


def _legacy_default_execute_service_tool() -> dict[str, Any]:
    """Return the historical default execute_services schema verbatim."""
    return {
        "spec": {
            "name": "execute_services",
            "description": "Execute service in Home Assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay": {
                        "type": "object",
                        "description": "Time to wait before execution",
                        "properties": {
                            "hours": {"type": "integer", "minimum": 0},
                            "minutes": {"type": "integer", "minimum": 0},
                            "seconds": {"type": "integer", "minimum": 0},
                        },
                    },
                    "list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "domain": {
                                    "type": "string",
                                    "description": "The domain of the service.",
                                },
                                "service": {
                                    "type": "string",
                                    "description": "The service to be called",
                                },
                                "service_data": {
                                    "type": "object",
                                    "description": (
                                        "The service data object to indicate what to "
                                        "control."
                                    ),
                                    "properties": {
                                        "entity_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": (
                                                    "The entity_id retrieved from "
                                                    "available devices. It must start "
                                                    "with domain, followed by dot "
                                                    "character."
                                                ),
                                            },
                                        },
                                        "area_id": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "description": (
                                                    "The id retrieved from areas. You "
                                                    "can specify only area_id without "
                                                    "entity_id to act on all entities "
                                                    "in that area"
                                                ),
                                            },
                                        },
                                    },
                                },
                            },
                            "required": ["domain", "service", "service_data"],
                        },
                    },
                },
            },
        },
        "function": {"type": "native", "name": "execute_service"},
    }


def _legacy_statistics_tool(periods: list[str] | None = None) -> dict[str, Any]:
    return {
        "spec": {
            "name": "get_statistics",
            "description": "Retrieve Home Assistant long-term statistics for a time period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "statistic_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 100,
                        "description": (
                            "Statistic IDs to retrieve. Entity-backed IDs must be "
                            "exposed to Assist; external integration statistics are "
                            "also supported."
                        ),
                    },
                    "start_time": {
                        "type": "string",
                        "description": "ISO 8601 start time.",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO 8601 end time.",
                    },
                    "period": {
                        "type": "string",
                        "enum": periods
                        or ["5minute", "hour", "day", "month"],
                    },
                    "units": {"type": "object"},
                    "types": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["statistic_ids", "start_time", "end_time"],
            },
        },
        "function": {"type": "native", "name": "get_statistics"},
    }


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
    if implementation == "execute_service":
        assert (
            tool["spec"]["parameters"]["properties"]["list"]["maxItems"]
            == MAX_NATIVE_SERVICE_ACTIONS
        )


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


def test_historical_default_service_schema_is_persistently_migrated() -> None:
    """The exact historical default becomes the visible current schema."""
    old = _legacy_default_execute_service_tool()

    migrated, changed = migrate_legacy_stock_native_function_tools([old])

    assert changed is True
    current = migrated[0]
    assert current["spec"]["name"] == "execute_services"
    assert current["spec"]["description"] == "Execute service in Home Assistant."
    assert current["spec"]["strict"] is False
    assert current["spec"]["parameters"]["required"] == ["list"]
    assert (
        current["spec"]["parameters"]["properties"]["list"]["maxItems"]
        == MAX_NATIVE_SERVICE_ACTIONS
    )
    service_data = _service_data_schema(current)
    assert service_data["additionalProperties"] is True
    assert set(service_data["properties"]) == {"entity_id", "area_id"}
    assert "color_temp_kelvin" in service_data["description"]


def test_stock_service_schema_migration_preserves_user_metadata() -> None:
    """Names, descriptions and enable/guest state do not block schema migration."""
    tool = _legacy_default_execute_service_tool()
    tool["spec"]["name"] = "house_service_control"
    tool["spec"]["description"] = "My deliberately customized model description."
    tool["enabled"] = False
    tool["guest_allowed"] = True
    original_function = deepcopy(tool["function"])

    migrated, changed = migrate_legacy_stock_native_function_tools([tool])

    assert changed is True
    current = migrated[0]
    assert current["spec"]["name"] == "house_service_control"
    assert (
        current["spec"]["description"]
        == "My deliberately customized model description."
    )
    assert current["enabled"] is False
    assert current["guest_allowed"] is True
    assert current["function"] == original_function
    assert current["spec"]["strict"] is False
    assert _service_data_schema(current)["additionalProperties"] is True


def test_yaml_migration_changes_only_function_tools_field_content() -> None:
    """Serialized migration preserves non-schema metadata and unrelated agent config."""
    tool = _legacy_default_execute_service_tool()
    tool["spec"]["name"] = "renamed_service_tool"
    tool["spec"]["description"] = "Keep this description."
    tool["enabled"] = False
    group_config = [
        {
            "id": "home_control",
            "name": "Home control",
            "description": "Keep this group untouched",
            "loading_mode": "on_demand",
            "functions": ["renamed_service_tool"],
            "enabled": False,
        }
    ]
    data = {
        CONF_FUNCTION_TOOLS: yaml.safe_dump([tool], sort_keys=False),
        "function_groups": deepcopy(group_config),
        "unrelated": {"keep": True},
    }
    before_groups = deepcopy(data["function_groups"])
    before_unrelated = deepcopy(data["unrelated"])

    migrated_yaml, changed = migrate_legacy_stock_native_function_tools_yaml(
        data[CONF_FUNCTION_TOOLS]
    )
    data[CONF_FUNCTION_TOOLS] = migrated_yaml

    assert changed is True
    migrated_tool = yaml.safe_load(data[CONF_FUNCTION_TOOLS])[0]
    assert migrated_tool["spec"]["name"] == "renamed_service_tool"
    assert migrated_tool["spec"]["description"] == "Keep this description."
    assert migrated_tool["enabled"] is False
    assert data["function_groups"] == before_groups
    assert data["unrelated"] == before_unrelated


def test_custom_service_schema_is_not_migrated_or_provider_rewritten() -> None:
    """Any schema customization remains authoritative end to end."""
    tool = _legacy_default_execute_service_tool()
    service_data = _service_data_schema(tool)
    service_data["description"] = "I intentionally customized this schema."
    original = deepcopy(tool)

    migrated, changed = migrate_legacy_stock_native_function_tools([tool])
    prepared = prepare_model_function_tools(migrated)[0]

    assert changed is False
    assert migrated == [original]
    assert prepared["spec"] == original["spec"]


def test_provider_receives_migrated_visible_service_schema_unchanged() -> None:
    """Provider formatting must not invent a second native schema representation."""
    migrated, changed = migrate_legacy_stock_native_function_tools(
        [_legacy_default_execute_service_tool()]
    )
    assert changed is True

    formatted = format_function_tools(migrated, API_MODE_RESPONSES)[0]

    assert formatted["strict"] is False
    assert formatted["parameters"] == migrated[0]["spec"]["parameters"]
    assert formatted["description"] == migrated[0]["spec"]["description"]
    assert formatted["name"] == migrated[0]["spec"]["name"]


def test_current_default_service_schema_is_already_visible_and_open() -> None:
    """New agents must not be seeded with the historical hidden-capability schema."""
    current = DEFAULT_CONF_FUNCTION_TOOLS[0]

    assert current["spec"]["strict"] is False
    assert current["spec"]["parameters"]["required"] == ["list"]
    assert (
        current["spec"]["parameters"]["properties"]["list"]["maxItems"]
        == MAX_NATIVE_SERVICE_ACTIONS
    )
    assert _service_data_schema(current)["additionalProperties"] is True


def test_statistics_preset_matches_current_home_assistant_contract() -> None:
    """The stock statistics preset exposes current recorder periods and value types."""
    tool = _preset_tool("get_statistics")
    properties = tool["spec"]["parameters"]["properties"]

    assert tool["spec"]["strict"] is False
    assert properties["period"]["enum"] == _STATISTICS_PERIODS
    assert properties["units"]["additionalProperties"] == {"type": "string"}
    assert properties["types"]["items"]["enum"] == _STATISTICS_TYPES


def test_legacy_stock_statistics_schema_is_persistently_migrated() -> None:
    """Previously inserted stock statistics YAML learns current HA capabilities."""
    saved = _legacy_statistics_tool()
    saved["spec"]["name"] = "my_statistics"
    saved["spec"]["description"] = "Keep my statistics wording."
    saved["enabled"] = False

    migrated, changed = migrate_legacy_stock_native_function_tools([saved])
    current = migrated[0]
    properties = current["spec"]["parameters"]["properties"]

    assert changed is True
    assert current["spec"]["name"] == "my_statistics"
    assert current["spec"]["description"] == "Keep my statistics wording."
    assert current["enabled"] is False
    assert current["spec"]["strict"] is False
    assert properties["period"]["enum"] == _STATISTICS_PERIODS
    assert properties["units"]["additionalProperties"] == {"type": "string"}
    assert properties["types"]["items"]["enum"] == _STATISTICS_TYPES


def test_custom_statistics_period_contract_is_not_migrated() -> None:
    """An intentionally customized statistics schema remains authoritative."""
    tool = _legacy_statistics_tool(["hour", "day"])
    original = deepcopy(tool)

    migrated, changed = migrate_legacy_stock_native_function_tools([tool])
    prepared = prepare_model_function_tools(migrated)[0]

    assert changed is False
    assert migrated == [original]
    assert prepared == original
