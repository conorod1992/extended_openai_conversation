"""Side-effect-free configuration guidance for the management UI."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_SKILLS,
    CONF_WEB_SEARCH,
    DEFAULT_API_MODE,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
)
from .exposed_attributes import exposed_attribute_catalog
from .helpers import supports_openai_hosted_tools
from .request import build_provider_request_snapshot
from .skill_availability import skill_loader_status

_CONFIGURATION_ACTIONS = {"get", "validate", "update", "save"}
_FUNCTION_REPAIR_CONFIGURATION_ACTIONS = {
    "configuration_get": "get",
    "configuration_validate": "validate",
    "configuration_save": "save",
}


def configuration_guidance_snapshot(
    entry_data: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    decoded_functions: bool = False,
) -> dict[str, Any]:
    """Return runtime-derived configuration facts without making provider calls."""
    configured_api_mode = str(options.get(CONF_API_MODE, DEFAULT_API_MODE))
    effective_api_mode = configured_api_mode
    hosted_tools_supported = supports_openai_hosted_tools(
        entry_data.get(CONF_API_PROVIDER), entry_data.get(CONF_BASE_URL)
    )

    # This is the production resolver with only the hosted feature forced on.
    probe = dict(options)
    probe[CONF_WEB_SEARCH] = True
    web_search_available = True
    web_search_message: str | None = None
    try:
        effective_api_mode = build_provider_request_snapshot(probe, entry_data).api_mode
    except HomeAssistantError as err:
        web_search_available = False
        web_search_message = str(err)
        with suppress(HomeAssistantError):
            effective_api_mode = build_provider_request_snapshot(
                {**options, CONF_WEB_SEARCH: False},
                entry_data,
                tools_required=True,
            ).api_mode

    reason: str | None
    if web_search_available:
        reason = None
    elif effective_api_mode != API_MODE_RESPONSES:
        reason = "requires_responses"
    elif not hosted_tools_supported:
        reason = "direct_openai_only"
    else:
        reason = "unavailable"

    guidance = {
        "effective_api_mode": effective_api_mode,
        "web_search": {
            "available": web_search_available,
            "reason": reason,
            "message": web_search_message,
            "hosted_tools_supported": hosted_tools_supported,
        },
    }
    if options.get(CONF_SKILLS):
        from .agent_config import (
            configured_function_tools_from_data,
            validate_function_groups,
            validate_function_tools,
        )

        try:
            # Configuration responses contain decoded collections. An empty list
            # there is explicit, unlike a legacy falsy persisted Functions field.
            tools = (
                validate_function_tools(options.get(CONF_FUNCTION_TOOLS))
                if decoded_functions
                else configured_function_tools_from_data(options)
            )
            groups = validate_function_groups(
                options.get(CONF_FUNCTION_GROUPS, []), tools
            )
            status = skill_loader_status(
                options[CONF_SKILLS],
                tools,
                groups,
                max_function_calls=options.get(
                    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                ),
            )
            available, message = status.available, status.reason
        except HomeAssistantError:
            available, message = (
                False,
                "Repair unavailable Function Tools before loading selected Skills",
            )
        guidance["skills"] = {
            "available": available,
            "message": message,
            "repair": (
                "Restore or enable the built-in load_skill Function Tool and its group, "
                "and allow at least one tool call per request; or clear the Skill selection. "
                "Existing settings are retained until you explicitly change them."
            ),
        }
    return guidance


def decorate_configuration_result(
    hass: HomeAssistant,
    entry_data: Mapping[str, Any],
    result: dict[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    """Attach the common dynamic metadata contract to one Configuration payload."""
    config = result.get("config")
    if not isinstance(config, dict):
        return result

    decorated = {
        **result,
        "configuration_guidance": configuration_guidance_snapshot(
            entry_data, config, decoded_functions=True
        ),
    }
    if action in {"update", "save"}:
        decorated["exposed_attribute_catalog"] = exposed_attribute_catalog(hass, config)
    return decorated


def _configuration_action(message: Mapping[str, Any]) -> str | None:
    """Normalize normal and Function Repair requests onto one Configuration action."""
    section = message.get("section", "overview")
    action = message.get("action")
    if section == "configuration" and action in _CONFIGURATION_ACTIONS:
        return str(action)
    if section == "function_repair":
        return _FUNCTION_REPAIR_CONFIGURATION_ACTIONS.get(str(action))
    return None
