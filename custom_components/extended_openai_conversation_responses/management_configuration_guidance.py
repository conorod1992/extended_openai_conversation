"""Side-effect-free configuration guidance for the management UI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_WEB_SEARCH,
    DEFAULT_API_MODE,
    DEFAULT_CHAT_MODEL,
)
from .exposed_attributes import exposed_attribute_catalog
from .helpers import get_api_mode, supports_openai_hosted_tools
from .request import build_web_search_tool

_CONFIGURATION_ACTIONS = {"get", "validate", "update", "save"}
_FUNCTION_REPAIR_CONFIGURATION_ACTIONS = {
    "configuration_get": "get",
    "configuration_validate": "validate",
    "configuration_save": "save",
}


def configuration_guidance_snapshot(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> dict[str, Any]:
    """Return runtime-derived configuration facts without making provider calls."""
    model = str(options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL))
    configured_api_mode = str(options.get(CONF_API_MODE, DEFAULT_API_MODE))
    effective_api_mode = get_api_mode(configured_api_mode, model)
    hosted_tools_supported = supports_openai_hosted_tools(
        entry_data.get(CONF_API_PROVIDER), entry_data.get(CONF_BASE_URL)
    )

    # Reuse the production request builder as the authority for whether Web Search
    # can actually be attached. Force the feature on only in this local probe; the
    # function is pure and performs no network request.
    probe = dict(options)
    probe[CONF_WEB_SEARCH] = True
    web_search_available = True
    web_search_message: str | None = None
    try:
        build_web_search_tool(probe, effective_api_mode, entry_data)
    except HomeAssistantError as err:
        web_search_available = False
        web_search_message = str(err)

    reason: str | None
    if web_search_available:
        reason = None
    elif effective_api_mode != API_MODE_RESPONSES:
        reason = "requires_responses"
    elif not hosted_tools_supported:
        reason = "direct_openai_only"
    else:
        reason = "unavailable"

    return {
        "effective_api_mode": effective_api_mode,
        "web_search": {
            "available": web_search_available,
            "reason": reason,
            "message": web_search_message,
            "hosted_tools_supported": hosted_tools_supported,
        },
    }


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
        "configuration_guidance": configuration_guidance_snapshot(entry_data, config),
    }
    if action in {"get", "update", "save"}:
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
