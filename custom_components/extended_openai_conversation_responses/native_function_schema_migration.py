"""Persistent migration for historical stock native Function Tool schemas."""

from __future__ import annotations

from copy import deepcopy
from types import MappingProxyType
from typing import Any, cast

import yaml

from .resource_limits import MAX_NATIVE_SERVICE_ACTIONS

SERVICE_DATA_DESCRIPTION = (
    "Any valid Home Assistant service data accepted by the selected service. Include "
    "a target such as entity_id, device_id, area_id, floor_id, or label_id. "
    "Service-specific fields are also allowed, for example brightness_pct, "
    "color_name, color_temp_kelvin, rgb_color, temperature, hvac_mode, transition, "
    "effect, or volume_level."
)
STATISTICS_PERIODS = ["5minute", "hour", "day", "week", "month", "year"]
STATISTICS_TYPES = [
    "change",
    "last_reset",
    "max",
    "mean",
    "min",
    "state",
    "sum",
]

_LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS: dict[str, Any] = {
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
                            "The service data object to indicate what to control."
                        ),
                        "properties": {
                            "entity_id": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "description": (
                                        "The entity_id retrieved from available "
                                        "devices. It must start with domain, followed "
                                        "by dot character."
                                    ),
                                },
                            },
                            "area_id": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "description": (
                                        "The id retrieved from areas. You can specify "
                                        "only area_id without entity_id to act on all "
                                        "entities in that area"
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
}

_LEGACY_PRESET_EXECUTE_SERVICE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "list": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "The Home Assistant service domain.",
                    },
                    "service": {
                        "type": "string",
                        "description": "The service name to call.",
                    },
                    "service_data": {
                        "type": "object",
                        "description": (
                            "Service data, including an entity_id, device_id, or "
                            "area_id target."
                        ),
                    },
                },
                "required": ["domain", "service", "service_data"],
            },
        }
    },
    "required": ["list"],
}

_LEGACY_PRESET_EXECUTE_SERVICE_SINGLE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {
            "type": "string",
            "description": "The Home Assistant service domain.",
        },
        "service": {"type": "string", "description": "The service name to call."},
        "service_data": {
            "type": "object",
            "description": (
                "Service data, including an entity_id, device_id, or area_id target."
            ),
        },
    },
    "required": ["domain", "service", "service_data"],
}

_LEGACY_PRESET_GET_STATISTICS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "statistic_ids": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 100,
            "description": (
                "Statistic IDs to retrieve. Entity-backed IDs must be exposed to "
                "Assist; external integration statistics are also supported."
            ),
        },
        "start_time": {"type": "string", "description": "ISO 8601 start time."},
        "end_time": {"type": "string", "description": "ISO 8601 end time."},
        "period": {
            "type": "string",
            "enum": ["5minute", "hour", "day", "month"],
        },
        "units": {"type": "object"},
        "types": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["statistic_ids", "start_time", "end_time"],
}


def _service_data_schema(
    parameters: dict[str, Any], implementation: str
) -> dict[str, Any]:
    """Return service_data from one already-recognized historical schema."""
    properties = cast(dict[str, Any], parameters["properties"])
    if implementation == "execute_service_single":
        return cast(dict[str, Any], properties["service_data"])
    list_schema = cast(dict[str, Any], properties["list"])
    item_schema = cast(dict[str, Any], list_schema["items"])
    item_properties = cast(dict[str, Any], item_schema["properties"])
    return cast(dict[str, Any], item_properties["service_data"])


def _migrate_service_schema(tool: dict[str, Any], implementation: str) -> bool:
    """Upgrade only an exact historical stock service schema."""
    spec = tool.get("spec")
    if not isinstance(spec, dict) or "strict" in spec:
        return False
    parameters = spec.get("parameters")
    if not isinstance(parameters, dict):
        return False

    legacy_default = parameters == _LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS
    historical: tuple[dict[str, Any], ...]
    if implementation == "execute_service":
        historical = (
            _LEGACY_DEFAULT_EXECUTE_SERVICE_PARAMETERS,
            _LEGACY_PRESET_EXECUTE_SERVICE_PARAMETERS,
        )
    else:
        historical = (_LEGACY_PRESET_EXECUTE_SERVICE_SINGLE_PARAMETERS,)
    if parameters not in historical:
        return False

    service_data = _service_data_schema(parameters, implementation)
    service_data["description"] = SERVICE_DATA_DESCRIPTION
    service_data["additionalProperties"] = True
    if implementation == "execute_service":
        properties = cast(dict[str, Any], parameters["properties"])
        list_schema = cast(dict[str, Any], properties["list"])
        list_schema["maxItems"] = MAX_NATIVE_SERVICE_ACTIONS
    if legacy_default:
        parameters["required"] = ["list"]
    spec["strict"] = False
    return True


def _migrate_statistics_schema(tool: dict[str, Any]) -> bool:
    """Upgrade only the exact historical stock statistics schema."""
    spec = tool.get("spec")
    if not isinstance(spec, dict) or "strict" in spec:
        return False
    parameters = spec.get("parameters")
    if parameters != _LEGACY_PRESET_GET_STATISTICS_PARAMETERS:
        return False
    if not isinstance(parameters, dict):
        return False

    properties = cast(dict[str, Any], parameters["properties"])
    cast(dict[str, Any], properties["period"]).update(
        {
            "enum": list(STATISTICS_PERIODS),
            "description": "Aggregation period; defaults to day.",
        }
    )
    cast(dict[str, Any], properties["units"]).update(
        {
            "description": (
                "Optional unit conversions keyed by Home Assistant unit class, "
                "for example power, energy, or temperature. Values are Home "
                "Assistant unit strings."
            ),
            "additionalProperties": {"type": "string"},
        }
    )
    cast(dict[str, Any], properties["types"]).update(
        {
            "items": {"type": "string", "enum": list(STATISTICS_TYPES)},
            "description": "Statistic value types to return; defaults to change.",
        }
    )
    spec["strict"] = False
    return True


def migrate_legacy_stock_native_function_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Persist current schemas only for exact historical stock native tools.

    Tool identity and presentation metadata are deliberately outside the fingerprint:
    spec.name, spec.description, enabled, guest_allowed, and Function Group membership
    may have been customized without changing the stock executable schema. They are
    therefore preserved value-for-value. Any schema change, including a strict setting
    or a changed parameter constraint, prevents migration.
    """
    migrated = deepcopy(tools)
    changed = False
    for tool in migrated:
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        implementation = function.get("name")
        if function != {"type": "native", "name": implementation}:
            continue
        if implementation in {"execute_service", "execute_service_single"}:
            changed = _migrate_service_schema(tool, implementation) or changed
        elif implementation == "get_statistics":
            changed = _migrate_statistics_schema(tool) or changed
    return migrated, changed


def migrate_legacy_stock_native_function_tools_yaml(value: Any) -> tuple[Any, bool]:
    """Return persistable YAML with exact historical stock schemas upgraded."""
    if not isinstance(value, str) or not value.strip():
        return value, False
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError:
        return value, False
    if not isinstance(parsed, list) or not all(
        isinstance(tool, dict) for tool in parsed
    ):
        return value, False

    migrated, changed = migrate_legacy_stock_native_function_tools(parsed)
    if not changed:
        return value, False
    return yaml.safe_dump(migrated, sort_keys=False, allow_unicode=True), True


def install_current_default_native_function_schemas() -> None:
    """Normalize in-memory defaults before new agents snapshot their configuration.

    The legacy constant remains the migration fingerprint in source, while every
    runtime consumer sees the current persisted schema. Existing saved agents are
    handled separately by the startup migration below; this function only prevents a
    newly-created agent from being seeded with a historical schema.
    """
    from . import agent_config, const

    migrated, changed = migrate_legacy_stock_native_function_tools(
        const.DEFAULT_CONF_FUNCTION_TOOLS
    )
    if not changed:
        return

    # Preserve the shared list identity imported by agent_config while replacing its
    # contents, then refresh the already-built authoritative default snapshot.
    const.DEFAULT_CONF_FUNCTION_TOOLS[:] = migrated
    defaults = dict(agent_config.AGENT_CONFIG_DEFAULTS)
    defaults[const.CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        migrated, sort_keys=False, allow_unicode=True
    )
    agent_config.AGENT_CONFIG_DEFAULTS = MappingProxyType(defaults)
