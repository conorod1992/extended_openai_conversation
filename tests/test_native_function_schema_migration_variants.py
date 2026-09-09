"""Additional exact-match coverage for native Function Tool schema migration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.native_function_schema_migration import (
    migrate_legacy_stock_native_function_tools,
)


def _legacy_service_preset(implementation: str) -> dict[str, Any]:
    service_data = {
        "type": "object",
        "description": (
            "Service data, including an entity_id, device_id, or area_id target."
        ),
    }
    if implementation == "execute_service_single":
        parameters = {
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
                "service_data": service_data,
            },
            "required": ["domain", "service", "service_data"],
        }
    else:
        parameters = {
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
                            "service_data": service_data,
                        },
                        "required": ["domain", "service", "service_data"],
                    },
                }
            },
            "required": ["list"],
        }
    return {
        "spec": {
            "name": f"custom_{implementation}",
            "description": "Keep this customized description.",
            "parameters": parameters,
        },
        "function": {"type": "native", "name": implementation},
        "enabled": False,
        "guest_allowed": True,
    }


def _service_data(tool: dict[str, Any]) -> dict[str, Any]:
    properties = tool["spec"]["parameters"]["properties"]
    if tool["function"]["name"] == "execute_service_single":
        return properties["service_data"]
    return properties["list"]["items"]["properties"]["service_data"]


@pytest.mark.parametrize("implementation", ["execute_service", "execute_service_single"])
def test_historical_inserted_service_preset_is_migrated(implementation: str) -> None:
    """Both historical built-in service presets receive the persistent open schema."""
    tool = _legacy_service_preset(implementation)

    migrated, changed = migrate_legacy_stock_native_function_tools([tool])

    assert changed is True
    current = migrated[0]
    assert current["spec"]["name"] == f"custom_{implementation}"
    assert current["spec"]["description"] == "Keep this customized description."
    assert current["enabled"] is False
    assert current["guest_allowed"] is True
    assert current["spec"]["strict"] is False
    assert _service_data(current)["additionalProperties"] is True


def test_native_function_config_customization_blocks_migration() -> None:
    """Only the exact stock native implementation mapping is migration-eligible."""
    tool = _legacy_service_preset("execute_service")
    tool["function"]["custom"] = True
    original = deepcopy(tool)

    migrated, changed = migrate_legacy_stock_native_function_tools([tool])

    assert changed is False
    assert migrated == [original]
