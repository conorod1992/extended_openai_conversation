"""Authoritative catalogue of user-exposable built-in Function Tool presets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .resource_limits import MAX_NATIVE_SERVICE_ACTIONS

_SERVICE_DATA_DESCRIPTION = (
    "Any valid Home Assistant service data accepted by the selected service. Include "
    "a target such as entity_id, device_id, area_id, floor_id, or label_id. "
    "Service-specific fields are also allowed, for example brightness_pct, "
    "color_name, color_temp_kelvin, rgb_color, temperature, hvac_mode, transition, "
    "effect, or volume_level."
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


def _preset(
    label: str,
    implementation: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    strict: bool | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
    }
    if required:
        parameters["required"] = required
    spec: dict[str, Any] = {
        "name": implementation,
        "description": description,
        "parameters": parameters,
    }
    if strict is not None:
        spec["strict"] = strict
    return {
        "label": label,
        "implementation": implementation,
        "tool": {
            "spec": spec,
            "function": {"type": "native", "name": implementation},
        },
    }


BUILT_IN_FUNCTION_PRESETS: tuple[dict[str, Any], ...] = (
    _preset(
        "Add automation",
        "add_automation",
        "Create a Home Assistant automation from a complete YAML configuration.",
        {
            "automation_config": {
                "type": "string",
                "description": "A complete Home Assistant automation in valid YAML.",
            }
        },
        ["automation_config"],
    ),
    _preset(
        "Send broadcast",
        "send_broadcast",
        (
            "Send a spoken Broadcast announcement to an Assist Satellite destination "
            "or the whole home. Use a natural Home Assistant area, device, floor, "
            "label, or satellite name as destination. Busy satellites are queued "
            "rather than interrupted."
        ),
        {
            "message": {
                "type": "string",
                "description": "The exact message to announce.",
            },
            "destination": {
                "type": "string",
                "description": (
                    "Target area, device, floor, label, or Assist Satellite name. "
                    "Omit only when whole_home is true."
                ),
            },
            "whole_home": {
                "type": "boolean",
                "description": (
                    "Send to every announcement-capable Assist Satellite except the "
                    "originating satellite."
                ),
            },
        },
        ["message"],
    ),
    _preset(
        "Execute services",
        "execute_service",
        (
            "Execute one or more Home Assistant services on exposed targets. Supply "
            "service-specific fields inside service_data when the requested action "
            "needs them."
        ),
        {
            "list": {
                "type": "array",
                "maxItems": MAX_NATIVE_SERVICE_ACTIONS,
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
                            "description": _SERVICE_DATA_DESCRIPTION,
                            "additionalProperties": True,
                        },
                    },
                    "required": ["domain", "service", "service_data"],
                },
            }
        },
        ["list"],
        strict=False,
    ),
    _preset(
        "Execute one service",
        "execute_service_single",
        (
            "Execute one Home Assistant service on an exposed target. Supply "
            "service-specific fields inside service_data when the requested action "
            "needs them."
        ),
        {
            "domain": {
                "type": "string",
                "description": "The Home Assistant service domain.",
            },
            "service": {"type": "string", "description": "The service name to call."},
            "service_data": {
                "type": "object",
                "description": _SERVICE_DATA_DESCRIPTION,
                "additionalProperties": True,
            },
        },
        ["domain", "service", "service_data"],
        strict=False,
    ),
    _preset(
        "Get entity history",
        "get_history",
        "Retrieve historical state data for exposed Home Assistant entities.",
        {
            "entity_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Entity IDs to retrieve history for.",
            },
            "start_time": {
                "type": "string",
                "description": "Optional ISO 8601 start time; defaults to one day ago.",
            },
            "end_time": {
                "type": "string",
                "description": (
                    "Optional ISO 8601 end time; defaults to one day after start_time."
                ),
            },
            "include_start_time_state": {"type": "boolean"},
            "significant_changes_only": {"type": "boolean"},
            "minimal_response": {"type": "boolean"},
            "no_attributes": {"type": "boolean"},
        },
        ["entity_ids"],
    ),
    _preset(
        "Get energy configuration",
        "get_energy",
        "Retrieve Home Assistant energy dashboard configuration and preferences.",
    ),
    _preset(
        "Get statistics",
        "get_statistics",
        "Retrieve Home Assistant long-term statistics for a time period.",
        {
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
                "enum": _STATISTICS_PERIODS,
                "description": "Aggregation period; defaults to day.",
            },
            "units": {
                "type": "object",
                "description": (
                    "Optional unit conversions keyed by Home Assistant unit class, "
                    "for example power, energy, or temperature. Values are Home "
                    "Assistant unit strings."
                ),
                "additionalProperties": {"type": "string"},
            },
            "types": {
                "type": "array",
                "items": {"type": "string", "enum": _STATISTICS_TYPES},
                "description": "Statistic value types to return; defaults to change.",
            },
        },
        ["statistic_ids", "start_time", "end_time"],
        strict=False,
    ),
    _preset(
        "Get current user",
        "get_user_from_user_id",
        "Return the current Home Assistant user's display name.",
    ),
)


def built_in_function_catalog(
    configured_tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return isolated catalogue metadata with configured implementations marked."""
    used = {
        tool.get("function", {}).get("name")
        for tool in configured_tools or []
        if tool.get("function", {}).get("type") == "native"
    }
    result = deepcopy(list(BUILT_IN_FUNCTION_PRESETS))
    for preset in result:
        preset["already_configured"] = preset["implementation"] in used
    return result
