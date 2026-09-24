"""Cheap, side-effect-free setup facts for the management Overview."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED,
    CONF_PROMPT,
    CONF_WEB_SEARCH,
    DEFAULT_API_MODE,
    DEFAULT_API_PROVIDER,
    DEFAULT_CHAT_MODEL,
    DEFAULT_PROMPT,
)
from .management_configuration_guidance import configuration_guidance_snapshot
from .management_function_repair import management_function_tool_health
from .memory import get_memory_mode


def _exposed_entity_count(hass: HomeAssistant) -> int:
    """Count entities globally exposed to Home Assistant Assist."""
    return sum(
        async_should_expose(hass, conversation.DOMAIN, state.entity_id)
        for state in hass.states.async_all()
    )


def _function_health(options: dict[str, Any]) -> dict[str, Any]:
    """Summarize Function Tool state through the metadata-only Management path."""
    return management_function_tool_health(options)


def build_setup_health_facts(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    subentry: ConfigSubentry,
    *,
    memory_available: bool,
    knowledge_source_count: int,
    knowledge_available: bool,
    is_admin: bool,
    function_tools_health: dict[str, Any] | None = None,
    include_exposed_entities: bool = True,
    include_function_tools: bool = True,
) -> dict[str, Any]:
    """Return only runtime facts the frontend cannot safely derive itself."""
    options = dict(subentry.data)
    prompt = str(options.get(CONF_PROMPT, DEFAULT_PROMPT))
    prompt_state = (
        "empty"
        if not prompt.strip()
        else "starter"
        if prompt.strip() == DEFAULT_PROMPT.strip()
        else "custom"
    )

    exposed_entity_count: int | None = None
    if include_exposed_entities:
        with suppress(Exception):
            exposed_entity_count = _exposed_entity_count(hass)

    guidance = configuration_guidance_snapshot(entry.data, options)
    return {
        "provider_runtime": {
            "client_loaded": getattr(entry, "runtime_data", None) is not None,
            "provider": str(entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER)),
            "model": str(options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)).strip(),
            "configured_api_mode": str(
                options.get(CONF_API_MODE, DEFAULT_API_MODE)
            ).strip(),
        },
        "function_tools": (
            function_tools_health
            if function_tools_health is not None
            else _function_health(options)
            if include_function_tools
            else {"loading": True}
        ),
        "prompt_state": prompt_state,
        "exposed_entity_count": exposed_entity_count,
        "memory": {
            "mode": get_memory_mode(options),
            "available": memory_available,
        },
        "knowledge": {
            "enabled": bool(options.get(CONF_KNOWLEDGE_ENABLED, False)),
            "source_count": knowledge_source_count,
            "available": knowledge_available,
        },
        "web_search": {
            "enabled": bool(options.get(CONF_WEB_SEARCH, False)),
            "effective_api_mode": guidance.get("effective_api_mode"),
            **dict(guidance.get("web_search", {})),
        },
        "can_manage": is_admin,
        "live_provider_tested": False,
    }


def add_setup_health(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    subentry: ConfigSubentry,
    result: dict[str, Any],
    *,
    is_admin: bool,
    function_tools_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add setup facts without turning a partial Overview into a request failure."""
    try:
        agent = result.get("agent", {})
        failed_keys = {
            issue.get("key")
            for issue in result.get("load_errors", [])
            if isinstance(issue, dict)
        }
        facts = build_setup_health_facts(
            hass,
            entry,
            subentry,
            memory_available="memories" not in failed_keys,
            knowledge_source_count=int(agent.get("knowledge_source_count", 0)),
            knowledge_available="knowledge" not in failed_keys,
            is_admin=is_admin,
            function_tools_health=function_tools_health,
        )
    except Exception:
        facts = {
            "unavailable": True,
            "provider_runtime": {
                "client_loaded": getattr(entry, "runtime_data", None) is not None,
                "provider": str(
                    entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER)
                ),
                "model": str(
                    subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
                ).strip(),
            },
            "can_manage": is_admin,
            "live_provider_tested": False,
        }
    return {**result, "setup_health": facts}
