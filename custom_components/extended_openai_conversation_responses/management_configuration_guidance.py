"""Side-effect-free configuration guidance for the management UI."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui
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
from .helpers import get_api_mode, supports_openai_hosted_tools
from .request import build_web_search_tool

_PATCHED = "extended_openai_management_configuration_guidance"
_FRONTEND_MODULE = "management-configuration-guidance.js"
ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
]


def _register_frontend_module() -> None:
    """Expose the guidance frontend alongside the existing management modules."""
    if _FRONTEND_MODULE in management_ui.MANAGEMENT_FRONTEND_MODULES:
        return
    management_ui.MANAGEMENT_FRONTEND_MODULES = (
        *management_ui.MANAGEMENT_FRONTEND_MODULES,
        _FRONTEND_MODULE,
    )


_register_frontend_module()


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


def wrap_management_configuration_guidance(
    original: ManagementCommand,
) -> ManagementCommand:
    """Attach guidance to successful configuration read/validate/update results."""

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        result = await original(hass, user_id, is_admin, message)
        if message.get("section", "overview") != "configuration":
            return result
        if message.get("action") not in {"get", "validate", "update"}:
            return result
        config = result.get("config") if isinstance(result, dict) else None
        if not isinstance(config, dict):
            return result

        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            return result
        entry, _subentry = management_ui.entry_and_agent(hass, entry_id, subentry_id)
        return {
            **result,
            "configuration_guidance": configuration_guidance_snapshot(
                getattr(entry, "data", {}), config
            ),
        }

    return wrapped


def install_management_configuration_guidance() -> bool:
    """Install the guidance wrapper exactly once."""
    _register_frontend_module()
    if getattr(management_ui, _PATCHED, False):
        return False
    management_ui.async_management_command = wrap_management_configuration_guidance(  # type: ignore[assignment]
        management_ui.async_management_command
    )
    setattr(management_ui, _PATCHED, True)
    return True
