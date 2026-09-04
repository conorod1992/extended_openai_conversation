"""Cheap, side-effect-free setup health for the management Overview."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_PROVIDER,
    CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED,
    CONF_PROMPT,
    CONF_WEB_SEARCH,
    DEFAULT_API_PROVIDER,
    DEFAULT_CHAT_MODEL,
    DEFAULT_PROMPT,
)
from .helpers import get_exposed_entities
from .management_configuration_guidance import configuration_guidance_snapshot
from .memory import get_memory_mode

_PATCHED = "extended_openai_management_setup_health"
OverviewCommand = Callable[..., Awaitable[dict[str, Any]]]


def _check(
    check_id: str,
    state: str,
    title: str,
    value: str,
    detail: str,
    *,
    page: str | None = None,
    subsection: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": check_id,
        "state": state,
        "title": title,
        "value": value,
        "detail": detail,
    }
    if page:
        result["action"] = {
            "page": page,
            "subsection": subsection,
            "target": target,
        }
    return result


def _overall_state(checks: list[dict[str, Any]]) -> str:
    states = {str(check.get("state")) for check in checks}
    if "error" in states:
        return "error"
    if "warning" in states or "unknown" in states:
        return "warning"
    return "ready"


def build_setup_health_snapshot(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    subentry: ConfigSubentry,
    *,
    knowledge_source_count: int,
    knowledge_available: bool,
    is_admin: bool,
) -> dict[str, Any]:
    """Return Overview health without provider calls, writes, or manager wakeups."""
    options = dict(subentry.data)
    provider = str(entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER))
    model = str(options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)).strip()
    client_loaded = getattr(entry, "runtime_data", None) is not None

    checks: list[dict[str, Any]] = [
        _check(
            "provider_runtime",
            "ready" if client_loaded else "error",
            "Provider runtime",
            f"{provider} · {model or 'No model selected'}",
            (
                "API client is loaded. Overview does not make a live provider request; use Diagnostics for an on-demand connection test."
                if client_loaded
                else "The provider API client is not currently available to this config entry."
            ),
            page="usage-maintenance",
            subsection="diagnostics",
        )
    ]

    prompt = str(options.get(CONF_PROMPT, DEFAULT_PROMPT))
    if not prompt.strip():
        checks.append(
            _check(
                "instructions",
                "warning",
                "Assistant instructions",
                "Empty",
                "No system instructions are configured for this assistant.",
                page="assistant",
                subsection="prompt-context",
                target="prompt-editor",
            )
        )
    elif prompt.strip() == DEFAULT_PROMPT.strip():
        checks.append(
            _check(
                "instructions",
                "ready",
                "Assistant instructions",
                "Starter instructions",
                "The built-in starter prompt is in use. Custom instructions are optional.",
                page="assistant",
                subsection="prompt-context",
                target="prompt-editor",
            )
        )
    else:
        checks.append(
            _check(
                "instructions",
                "ready",
                "Assistant instructions",
                "Customised",
                "This assistant uses customised system instructions.",
                page="assistant",
                subsection="prompt-context",
                target="prompt-editor",
            )
        )

    try:
        exposed_count = len(get_exposed_entities(hass))
    except Exception:
        checks.append(
            _check(
                "home_assistant_exposure",
                "unknown",
                "Home Assistant access",
                "Unable to determine",
                "Overview could not count the entities currently exposed to Assist.",
                page="capabilities",
                subsection="home-assistant",
            )
        )
    else:
        checks.append(
            _check(
                "home_assistant_exposure",
                "ready" if exposed_count else "warning",
                "Home Assistant access",
                f"{exposed_count:,} entities exposed to Assist",
                (
                    "These are the entities currently available through Home Assistant's Assist exposure rules."
                    if exposed_count
                    else "No entities are currently exposed to Assist. The assistant can still answer non-device questions, but Home Assistant device access will be limited."
                ),
                page="capabilities",
                subsection="home-assistant",
            )
        )

    memory_mode = get_memory_mode(options)
    checks.append(
        _check(
            "memory",
            "neutral" if memory_mode == "off" else "ready",
            "Persistent memory",
            "Off by choice"
            if memory_mode == "off"
            else memory_mode.replace("_", " ").title(),
            (
                "Persistent memory is optional and is currently disabled."
                if memory_mode == "off"
                else "Persistent memory is enabled for this assistant."
            ),
            page="data-memory",
            subsection="memory-settings",
        )
    )

    knowledge_enabled = bool(options.get(CONF_KNOWLEDGE_ENABLED, False))
    if not knowledge_enabled:
        checks.append(
            _check(
                "knowledge",
                "neutral",
                "Knowledge Library",
                "Off by choice",
                "Knowledge Library access is optional and is currently disabled.",
                page="data-memory",
                subsection="knowledge",
            )
        )
    elif not knowledge_available:
        checks.append(
            _check(
                "knowledge",
                "unknown",
                "Knowledge Library",
                "Unable to determine",
                "Knowledge Library is enabled, but Overview could not load the stored-source count.",
                page="data-memory",
                subsection="knowledge",
            )
        )
    elif knowledge_source_count:
        checks.append(
            _check(
                "knowledge",
                "ready",
                "Knowledge Library",
                f"{knowledge_source_count:,} source{'s' if knowledge_source_count != 1 else ''}",
                "Knowledge Library access is enabled and has stored reference material.",
                page="data-memory",
                subsection="knowledge",
            )
        )
    else:
        checks.append(
            _check(
                "knowledge",
                "warning",
                "Knowledge Library",
                "Enabled, no sources",
                "Knowledge Library access is enabled but there are no stored sources to search.",
                page="data-memory",
                subsection="knowledge",
            )
        )

    guidance = configuration_guidance_snapshot(entry.data, options)
    web_search_enabled = bool(options.get(CONF_WEB_SEARCH, False))
    web_status = guidance.get("web_search", {})
    if not web_search_enabled:
        checks.append(
            _check(
                "web_search",
                "neutral",
                "Web Search",
                "Off by choice",
                "Hosted Web Search is optional and is currently disabled.",
                page="capabilities",
                subsection="web-skills",
            )
        )
    elif web_status.get("available") is False:
        requires_responses = web_status.get("reason") == "requires_responses"
        checks.append(
            _check(
                "web_search",
                "warning",
                "Web Search",
                "Needs attention",
                str(
                    web_status.get("message")
                    or "The current provider configuration cannot attach hosted Web Search."
                ),
                page="assistant" if requires_responses else "capabilities",
                subsection="basics" if requires_responses else "web-skills",
                target="config-api_mode" if requires_responses else "config-web_search",
            )
        )
    else:
        checks.append(
            _check(
                "web_search",
                "ready",
                "Web Search",
                "Available",
                "The current provider/API configuration can attach hosted Web Search when requested.",
                page="capabilities",
                subsection="web-skills",
            )
        )

    state = _overall_state(checks)
    warning_count = sum(check.get("state") == "warning" for check in checks)
    error_count = sum(check.get("state") == "error" for check in checks)
    unknown_count = sum(check.get("state") == "unknown" for check in checks)
    return {
        "state": state,
        "summary": (
            "Needs attention"
            if state == "error"
            else "Review recommended"
            if state == "warning"
            else "Ready"
        ),
        "warning_count": warning_count,
        "error_count": error_count,
        "unknown_count": unknown_count,
        "can_manage": is_admin,
        "checks": checks,
        "live_provider_tested": False,
    }


def install_management_setup_health() -> bool:
    """Attach setup health to the optimized Overview response exactly once."""
    from . import management_loading_performance, management_ui

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
        entry, subentry = management_ui.entry_and_agent(
            hass, message.get("entry_id"), message.get("subentry_id")
        )
        agent = result.get("agent") if isinstance(result, dict) else None
        knowledge_source_count = (
            int(agent.get("knowledge_source_count", 0))
            if isinstance(agent, dict)
            else 0
        )
        load_errors = result.get("load_errors", []) if isinstance(result, dict) else []
        knowledge_available = not any(
            isinstance(issue, dict) and issue.get("key") == "knowledge"
            for issue in load_errors
        )
        return {
            **result,
            "setup_health": build_setup_health_snapshot(
                hass,
                entry,
                subentry,
                knowledge_source_count=knowledge_source_count,
                knowledge_available=knowledge_available,
                is_admin=is_admin,
            ),
        }

    management_loading_performance.async_overview_summary = wrapped  # type: ignore[assignment]
    setattr(management_loading_performance, _PATCHED, True)
    return True
