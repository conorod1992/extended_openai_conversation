"""Cheap, side-effect-free setup facts for the management Overview."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

from . import management_ui
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
from .memory import get_memory_mode

_PATCHED = "extended_openai_management_setup_health"
_FRONTEND_MODULES = ("overview-health.js", "overview-onboarding.js")
OverviewCommand = Callable[..., Awaitable[dict[str, Any]]]


def _register_frontend_modules() -> None:
    """Expose Overview setup presentation helpers before UI static paths exist."""
    modules = tuple(
        dict.fromkeys((*management_ui.MANAGEMENT_FRONTEND_MODULES, *_FRONTEND_MODULES))
    )
    setattr(management_ui, "MANAGEMENT_FRONTEND_MODULES", modules)  # noqa: B010


_register_frontend_modules()


def _exposed_entity_count(hass: HomeAssistant) -> int:
    """Count entities globally exposed to Home Assistant Assist."""
    return sum(
        async_should_expose(hass, conversation.DOMAIN, state.entity_id)
        for state in hass.states.async_all()
    )


def build_setup_health_facts(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    subentry: ConfigSubentry,
    *,
    memory_available: bool,
    knowledge_source_count: int,
    knowledge_available: bool,
    is_admin: bool,
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

    try:
        exposed_entity_count: int | None = _exposed_entity_count(hass)
    except Exception:
        exposed_entity_count = None

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


def install_management_setup_health() -> bool:
    """Attach setup facts to the optimized Overview response exactly once."""
    from . import management_loading_performance

    if getattr(management_loading_performance, _PATCHED, False):
        return False
    original: OverviewCommand = management_loading_performance.async_overview_summary

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        result = await original(hass, user_id, is_admin, message)
        try:
            entry_id = message.get("entry_id")
            subentry_id = message.get("subentry_id")
            if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
                raise ValueError("entry_id and subentry_id are required")
            entry, subentry = management_ui.entry_and_agent(hass, entry_id, subentry_id)
            agent = result.get("agent") if isinstance(result, dict) else None
            knowledge_source_count = (
                int(agent.get("knowledge_source_count", 0))
                if isinstance(agent, dict)
                else 0
            )
            load_errors = (
                result.get("load_errors", []) if isinstance(result, dict) else []
            )
            failed_keys = {
                issue.get("key") for issue in load_errors if isinstance(issue, dict)
            }
            setup_health = build_setup_health_facts(
                hass,
                entry,
                subentry,
                memory_available="memories" not in failed_keys,
                knowledge_source_count=knowledge_source_count,
                knowledge_available="knowledge" not in failed_keys,
                is_admin=is_admin,
            )
        except Exception:
            # Setup health is additive. Never turn a failure in this summary layer
            # into an Overview failure when the original Overview data is usable.
            setup_health = {
                "unavailable": True,
                "can_manage": is_admin,
                "live_provider_tested": False,
            }
        return {**result, "setup_health": setup_health}

    management_loading_performance.async_overview_summary = wrapped  # type: ignore[assignment]
    setattr(management_loading_performance, _PATCHED, True)
    return True
