"""Startup persistence coverage for historical stock native Function Tool schemas."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import MagicMock, patch

import yaml

from custom_components.extended_openai_conversation_responses import (
    async_migrate_integration,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONFIG_ENTRY_VERSION,
)


def _historical_execute_service_preset() -> dict:
    return {
        "spec": {
            "name": "renamed_service_tool",
            "description": "Keep my custom description.",
            "parameters": {
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
                                        "Service data, including an entity_id, "
                                        "device_id, or area_id target."
                                    ),
                                },
                            },
                            "required": ["domain", "service", "service_data"],
                        },
                    }
                },
                "required": ["list"],
            },
        },
        "function": {"type": "native", "name": "execute_service"},
        "enabled": False,
        "guest_allowed": True,
    }


async def test_startup_persists_schema_without_touching_group_or_metadata(hass) -> None:
    """Current-version entries receive the exact-stock migration before agents load."""
    tool = _historical_execute_service_preset()
    groups = [
        {
            "id": "home_control",
            "name": "Renamed group",
            "description": "Keep this group exactly as configured.",
            "loading_mode": "on_demand",
            "functions": ["renamed_service_tool"],
            "enabled": False,
        }
    ]
    subentry = MagicMock()
    subentry.subentry_type = "conversation"
    subentry.data = {
        CONF_FUNCTION_TOOLS: yaml.safe_dump([tool], sort_keys=False),
        CONF_FUNCTION_GROUPS: deepcopy(groups),
        "unrelated": "keep-me",
    }
    entry = MagicMock()
    entry.disabled_by = None
    entry.version = CONFIG_ENTRY_VERSION
    entry.subentries = {"agent": subentry}

    with (
        patch.object(hass.config_entries, "async_entries", return_value=[entry]),
        patch.object(hass.config_entries, "async_update_subentry") as update_subentry,
    ):
        await async_migrate_integration(hass)

    update_subentry.assert_called_once()
    call = update_subentry.call_args
    assert call.args[:2] == (entry, subentry)
    saved = call.kwargs["data"]
    migrated_tool = yaml.safe_load(saved[CONF_FUNCTION_TOOLS])[0]

    assert migrated_tool["spec"]["name"] == "renamed_service_tool"
    assert migrated_tool["spec"]["description"] == "Keep my custom description."
    assert migrated_tool["enabled"] is False
    assert migrated_tool["guest_allowed"] is True
    assert migrated_tool["spec"]["strict"] is False
    service_data = migrated_tool["spec"]["parameters"]["properties"]["list"][
        "items"
    ]["properties"]["service_data"]
    assert service_data["additionalProperties"] is True
    assert saved[CONF_FUNCTION_GROUPS] == groups
    assert saved["unrelated"] == "keep-me"
