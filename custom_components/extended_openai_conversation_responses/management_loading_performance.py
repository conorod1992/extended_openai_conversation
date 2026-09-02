"""Low-risk loading and network optimizations for the management frontend."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
import sys
from typing import Any

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_PROVIDER,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    DEFAULT_API_PROVIDER,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_CHAT_MODEL,
    DEFAULT_FUNCTION_GROUPS,
    DOMAIN,
    MANAGEMENT_PANEL_TITLE,
    MANAGEMENT_PANEL_URL,
)
from .frontend_version import FRONTEND_VERSION
from .guest_mode import async_get_guest_mode, get_loaded_guest_mode
from .knowledge import async_get_knowledge
from .memory import async_get_memory, get_memory_mode
from .usage import async_get_usage

_INSTALLED = False
_ORIGINAL_MANAGEMENT_COMMAND: Callable[..., Awaitable[dict[str, Any]]] | None = None
_EXTRA_FRONTEND_MODULES = (
    "management-rendering-performance.js",
    "management-loading-performance.js",
    "overview-page-impl.js",
    "guide-page-impl.js",
)


def _asset_url(module_name: str) -> str:
    """Return an immutable, release-versioned frontend module URL."""
    return f"/{DOMAIN}/assets/{FRONTEND_VERSION}/{module_name}"


def _guest_has_ha_exclusions(options: dict[str, Any]) -> bool:
    return any(
        options.get(key)
        for key in (
            "guest_excluded_labels",
            "guest_excluded_areas",
            "guest_excluded_domains",
            "guest_excluded_entities",
        )
    )


def _agent_snapshot(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    memory_count: int = 0,
    knowledge_source_count: int = 0,
    tokens_today: int = 0,
    guest_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the cheap frontend metadata shared by bootstrap and overview."""
    options = config if config is not None else dict(subentry.data)
    management_ui = _management_ui()
    configured = options.get(CONF_FUNCTION_TOOLS)
    configured_tools = (
        management_ui.validate_function_tools(configured)
        if isinstance(configured, list)
        else management_ui.configured_function_tools_from_data(options)
    )
    if guest_status is None:
        loaded_guest = get_loaded_guest_mode(hass, entry.entry_id, subentry.subentry_id)
        guest_status = (
            dict(loaded_guest.status())
            if loaded_guest is not None
            else {"state": "unloaded", "currently_active": False}
        )
    else:
        guest_status = dict(guest_status)
    guest_status["has_home_assistant_exclusions"] = _guest_has_ha_exclusions(options)
    return {
        "entry_id": entry.entry_id,
        "entry_title": entry.title,
        "subentry_id": subentry.subentry_id,
        "title": title if title is not None else subentry.title,
        "provider": entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER),
        "model": options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
        "memory_mode": get_memory_mode(options),
        "memory_count": memory_count,
        "knowledge_enabled": bool(options.get(CONF_KNOWLEDGE_ENABLED, False)),
        "knowledge_source_count": knowledge_source_count,
        "function_count": sum(
            management_ui.function_tool_enabled(tool) for tool in configured_tools
        ),
        "function_group_count": len(
            options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)
        ),
        "archive_enabled": bool(
            options.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        ),
        "tokens_today": tokens_today,
        "guest_mode": guest_status,
    }


def _management_ui():
    from . import management_ui

    return management_ui


def _debug_ui():
    from . import debug_ui

    return debug_ui


async def async_agent_catalog(
    hass: HomeAssistant, user_id: str, is_admin: bool
) -> dict[str, Any]:
    """Return navigation metadata without waking every per-agent data manager."""
    management_ui = _management_ui()
    agents = [
        _agent_snapshot(hass, entry, subentry)
        for entry in hass.config_entries.async_entries(DOMAIN)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    ]
    return {
        "agents": agents,
        "scopes": await management_ui._scope_catalog(hass, user_id, is_admin),
        "is_admin": is_admin,
    }


async def async_overview_summary(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Load selected-agent overview data concurrently in one websocket request."""
    del user_id, is_admin
    management_ui = _management_ui()
    requested_entry_id = message.get("entry_id")
    requested_subentry_id = message.get("subentry_id")
    entry, subentry = management_ui.entry_and_agent(
        hass, requested_entry_id, requested_subentry_id
    )
    entry_id = str(entry.entry_id)
    subentry_id = str(subentry.subentry_id)

    usage_result, memory_result, knowledge_result, guest_result = await asyncio.gather(
        async_get_usage(hass, entry_id, subentry_id),
        async_get_memory(hass, entry_id, subentry_id),
        async_get_knowledge(hass, entry_id, subentry_id),
        async_get_guest_mode(hass, entry_id, subentry_id),
        return_exceptions=True,
    )

    load_errors: list[dict[str, str]] = []

    def record_failure(key: str, label: str, error: BaseException) -> None:
        load_errors.append(
            {
                "key": key,
                "label": label,
                "message": str(error) or type(error).__name__,
            }
        )

    usage: dict[str, Any] = {}
    tokens_today = 0
    if isinstance(usage_result, BaseException):
        record_failure("usage", "Usage", usage_result)
    else:
        usage = {
            "lifetime": usage_result.as_dict(),
            "today": usage_result.today_summary(),
            "month": usage_result.month_summary(),
            "latest": management_ui.asdict_or_none(usage_result.latest_run),
        }
        tokens_today = int(usage["today"].get("total_tokens", 0))

    memory_count = 0
    if isinstance(memory_result, BaseException):
        record_failure("memories", "Memory", memory_result)
    else:
        memory_count = int(memory_result.stats().get("memory_count", 0))

    knowledge_source_count = 0
    if isinstance(knowledge_result, BaseException):
        record_failure("knowledge", "Knowledge", knowledge_result)
    else:
        knowledge_source_count = int(knowledge_result.source_count)

    guest_status: dict[str, Any] | None = None
    if isinstance(guest_result, BaseException):
        record_failure("guest_mode", "Guest Mode", guest_result)
    else:
        guest_status = guest_result.status()

    return {
        "agent": _agent_snapshot(
            hass,
            entry,
            subentry,
            memory_count=memory_count,
            knowledge_source_count=knowledge_source_count,
            tokens_today=tokens_today,
            guest_status=guest_status,
        ),
        "usage": usage,
        "conversations": management_ui._settings_snapshot(dict(subentry.data)),
        "load_errors": load_errors,
    }


async def _async_save_configuration(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    message: dict[str, Any],
    original: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Validate and persist configuration behind one frontend websocket call."""
    title = message.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        return {"valid": False, "errors": {"title": "must not be empty"}}

    validation = await original(
        hass,
        user_id,
        is_admin,
        {**message, "action": "validate"},
    )
    if not validation.get("valid"):
        return validation

    saved = await original(
        hass,
        user_id,
        is_admin,
        {**message, "action": "update"},
    )
    management_ui = _management_ui()
    entry, subentry = management_ui.entry_and_agent(
        hass, message.get("entry_id"), message.get("subentry_id")
    )
    return {
        "valid": True,
        "errors": {},
        **saved,
        "agent": _agent_snapshot(
            hass,
            entry,
            subentry,
            config=saved["config"],
            title=saved["title"],
        ),
    }


async def optimized_management_command(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch optimized read paths while preserving the existing API fallback."""
    original = _ORIGINAL_MANAGEMENT_COMMAND
    if original is None:
        original = _management_ui().async_management_command

    if message.get("action") == "agents":
        return await async_agent_catalog(hass, user_id, is_admin)
    if message.get("section") == "overview" and message.get("action") == "summary":
        return await async_overview_summary(hass, user_id, is_admin, message)
    if message.get("section") == "configuration" and message.get("action") == "save":
        return await _async_save_configuration(
            hass, user_id, is_admin, message, original
        )
    return await original(hass, user_id, is_admin, message)


def _static_paths(
    frontend_dir: Path, module_names: tuple[str, ...]
) -> list[StaticPathConfig]:
    """Keep legacy no-cache aliases while adding immutable versioned URLs."""
    paths: list[StaticPathConfig] = []
    for module_name in module_names:
        file_path = str(frontend_dir / module_name)
        paths.append(
            StaticPathConfig(f"/{DOMAIN}/{module_name}", file_path, cache_headers=False)
        )
        paths.append(
            StaticPathConfig(_asset_url(module_name), file_path, cache_headers=True)
        )
    return paths


async def async_setup_cached_management_ui(hass: HomeAssistant) -> None:
    """Register the management panel with immutable release-versioned assets."""
    management_ui = _management_ui()
    if hass.data.get(management_ui._UI_SETUP):
        return
    hass.data[management_ui._UI_SETUP] = True
    frontend_dir = Path(management_ui.__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        _static_paths(frontend_dir, management_ui.MANAGEMENT_FRONTEND_MODULES)
    )
    websocket_api.async_register_command(hass, management_ui.websocket_management)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="extended-openai-management-panel",
        frontend_url_path=MANAGEMENT_PANEL_URL,
        module_url=_asset_url("management-panel.js"),
        sidebar_title=MANAGEMENT_PANEL_TITLE,
        sidebar_icon="mdi:robot-outline",
        require_admin=False,
    )


async def async_setup_cached_debug_ui(hass: HomeAssistant) -> None:
    """Register debug modules at legacy and immutable versioned asset URLs."""
    debug_ui = _debug_ui()
    if hass.data.get(debug_ui._DEBUG_UI_SETUP):
        return
    hass.data[debug_ui._DEBUG_UI_SETUP] = True
    frontend_dir = Path(debug_ui.__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        _static_paths(frontend_dir, ("debug-panel.js", "debug-management.js"))
    )
    websocket_api.async_register_command(hass, debug_ui.websocket_request_debug)


def install_management_loading_optimizations() -> None:
    """Install frontend loading optimizations before management UI setup."""
    global _INSTALLED, _ORIGINAL_MANAGEMENT_COMMAND
    if _INSTALLED:
        return
    _INSTALLED = True

    management_ui = _management_ui()
    debug_ui = _debug_ui()
    _ORIGINAL_MANAGEMENT_COMMAND = management_ui.async_management_command
    management_ui.async_management_command = optimized_management_command  # type: ignore[assignment]

    management_ui.MANAGEMENT_FRONTEND_MODULES = tuple(
        dict.fromkeys(
            (*management_ui.MANAGEMENT_FRONTEND_MODULES, *_EXTRA_FRONTEND_MODULES)
        )
    )
    management_ui.async_setup_management_ui = async_setup_cached_management_ui  # type: ignore[assignment]
    debug_ui.async_setup_debug_ui = async_setup_cached_debug_ui  # type: ignore[assignment]

    package = sys.modules.get(__package__)
    if package is not None:
        setattr(  # noqa: B010
            package, "async_setup_management_ui", async_setup_cached_management_ui
        )
        setattr(package, "async_setup_debug_ui", async_setup_cached_debug_ui)  # noqa: B010
