"""Authoritative catalogue of user-exposable built-in Function Tool presets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _preset(
    label: str,
    implementation: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
    }
    if required:
        parameters["required"] = required
    return {
        "label": label,
        "implementation": implementation,
        "tool": {
            "spec": {
                "name": implementation,
                "description": description,
                "parameters": parameters,
            },
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
        "Send a spoken Broadcast announcement to an Assist Satellite destination or the whole home. Use a natural Home Assistant area, device, floor, label, or satellite name as destination. Busy satellites are queued rather than interrupted.",
        {
            "message": {
                "type": "string",
                "description": "The exact message to announce.",
            },
            "destination": {
                "type": "string",
                "description": "Target area, device, floor, label, or Assist Satellite name. Omit only when whole_home is true.",
            },
            "whole_home": {
                "type": "boolean",
                "description": "Send to every announcement-capable Assist Satellite except the originating satellite.",
            },
        },
        ["message"],
    ),
    _preset(
        "Execute services",
        "execute_service",
        "Execute one or more Home Assistant services on exposed entities, devices, or areas.",
        {
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
                            "description": "Service data, including an entity_id, device_id, or area_id target.",
                        },
                    },
                    "required": ["domain", "service", "service_data"],
                },
            }
        },
        ["list"],
    ),
    _preset(
        "Execute one service",
        "execute_service_single",
        "Execute one Home Assistant service on an exposed entity, device, or area.",
        {
            "domain": {
                "type": "string",
                "description": "The Home Assistant service domain.",
            },
            "service": {"type": "string", "description": "The service name to call."},
            "service_data": {
                "type": "object",
                "description": "Service data, including an entity_id, device_id, or area_id target.",
            },
        },
        ["domain", "service", "service_data"],
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
                "description": "Optional ISO 8601 end time.",
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
                "description": "Statistic IDs to retrieve. Entity-backed IDs must be exposed to Assist; external integration statistics are also supported.",
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
        ["statistic_ids", "start_time", "end_time"],
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
