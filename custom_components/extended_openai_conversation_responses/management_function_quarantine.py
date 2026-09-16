"""Keep unrelated management surfaces usable while Function Tools need repair."""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from types import SimpleNamespace
from typing import Any, cast

import yaml

from homeassistant.core import HomeAssistant

from . import function_dependency_integrity, management_setup_health, management_ui
from .agent_test import AgentTestResult, TestCheck, _overall
from .const import (
    CONF_API_PROVIDER,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    DEFAULT_API_PROVIDER,
    DEFAULT_CHAT_MODEL,
    DEFAULT_FUNCTION_GROUPS,
)
from .management_function_repair import function_tools_issue, isolated_function_tools

_PATCHED = "extended_openai_management_function_quarantine"
_OVERVIEW_PATCHED = "extended_openai_management_function_quarantine_overview"
_ALLOW_QUARANTINED_TOOLS: ContextVar[bool] = ContextVar(
    "extended_openai_management_allow_quarantined_tools", default=False
)

# This module is imported before management_ui registers static paths, so keep the
# UX helpers introduced alongside quarantine available to Home Assistant as served
# frontend assets as well as management-bootstrap dependencies.
management_ui.MANAGEMENT_FRONTEND_MODULES = tuple(
    dict.fromkeys(
        (
            *management_ui.MANAGEMENT_FRONTEND_MODULES,
            "management-conversation-default-label.js",
            "management-overview-health-clarity.js",
        )
    )
)

_STRICT_CONFIGURED_TOOLS = management_ui.configured_function_tools_from_data
_STRICT_MERGE_AGENT_CONFIG = management_ui.merge_agent_config
_ORIGINAL_AGENT_TEST = management_ui.async_test_agent


def _safe_function_configuration(data: dict[str, Any]) -> dict[str, Any]:
    """Return a management-only copy with invalid Function Tools excluded."""
    valid, _invalid, _issue = isolated_function_tools(data)
    safe = dict(data)
    safe[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        valid, sort_keys=False, allow_unicode=True
    )
    valid_names = {
        tool["spec"]["name"]
        for tool in valid
        if isinstance(tool, dict)
        and isinstance(tool.get("spec"), dict)
        and isinstance(tool["spec"].get("name"), str)
    }
    raw_groups = deepcopy(data.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS))
    if isinstance(raw_groups, list):
        for group in raw_groups:
            if not isinstance(group, dict) or not isinstance(
                group.get("functions"), list
            ):
                continue
            group["functions"] = [
                name for name in group["functions"] if name in valid_names
            ]
    safe[CONF_FUNCTION_GROUPS] = raw_groups
    return safe


def _usable_function_tools(data: Any) -> list[dict[str, Any]]:
    """Return valid siblings when persisted Function Tools contain repairable errors."""
    tools, issue = function_tools_issue(dict(data))
    if issue is not None:
        return tools
    return _STRICT_CONFIGURED_TOOLS(data)


def _management_configured_tools(data: Any) -> list[dict[str, Any]]:
    """Use tolerant Function Tool loading only inside isolated management sections."""
    if _ALLOW_QUARANTINED_TOOLS.get():
        return _usable_function_tools(data)
    return _STRICT_CONFIGURED_TOOLS(data)


def _dependency_configured_tools(data: Any) -> list[dict[str, Any]]:
    """Validate Request Rule references against usable siblings, not broken tools."""
    return _usable_function_tools(data)


def _management_merge_agent_config(
    source: Any, updates: dict[str, Any]
) -> dict[str, Any]:
    """Persist unrelated section edits without rewriting repair-owned Function fields."""
    if not _ALLOW_QUARANTINED_TOOLS.get():
        return _STRICT_MERGE_AGENT_CONFIG(source, updates)

    raw = dict(source)
    _tools, issue = function_tools_issue(raw)
    if issue is None:
        return _STRICT_MERGE_AGENT_CONFIG(source, updates)

    normalized = _STRICT_MERGE_AGENT_CONFIG(_safe_function_configuration(raw), updates)
    for key in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS):
        if key in raw:
            normalized[key] = deepcopy(raw[key])
        else:
            normalized.pop(key, None)
    return normalized


async def _tolerant_agent_test(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> AgentTestResult:
    """Run provider diagnostics with valid Function Tool siblings only."""
    raw = dict(subentry.data)
    _valid, invalid, issue = isolated_function_tools(raw)
    if issue is None:
        return await _ORIGINAL_AGENT_TEST(hass, entry, subentry)

    safe_subentry = SimpleNamespace(
        data=_safe_function_configuration(raw),
        subentry_id=subentry.subentry_id,
        title=getattr(subentry, "title", ""),
    )
    result = await _ORIGINAL_AGENT_TEST(hass, entry, cast(Any, safe_subentry))
    if invalid:
        names = [
            str(item.get("name") or f"tool {int(item.get('index', 0)) + 1}")
            for item in invalid
        ]
        detail = (
            f"{len(invalid)} invalid Function Tool"
            f"{'s' if len(invalid) != 1 else ''} quarantined: "
            + ", ".join(names[:5])
            + ("…" if len(names) > 5 else "")
        )
    else:
        detail = "Invalid Function Tool configuration was quarantined for this test"
    result.checks.append(TestCheck("Function Tools", "Warning", detail))
    result.status = _overall(result.checks)
    return result


def _wrap_management_command(original):
    """Scope tolerant Function Tool reads to Request Rules and Guest Mode."""

    @wraps(original)
    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        if message.get("section") not in {"request_rules", "guest_mode"}:
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )
        token = _ALLOW_QUARANTINED_TOOLS.set(True)
        try:
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )
        finally:
            _ALLOW_QUARANTINED_TOOLS.reset(token)

    return wrapped


def _install_overview_provider_fallback() -> None:
    """Never lose persisted provider/model identity to an unrelated config failure."""
    from . import management_loading_performance

    if getattr(management_loading_performance, _OVERVIEW_PATCHED, False):
        return
    original = management_loading_performance.async_overview_summary

    @wraps(original)
    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        result = await original(hass, user_id, is_admin, message)
        facts = result.get("setup_health") if isinstance(result, dict) else None
        runtime = facts.get("provider_runtime") if isinstance(facts, dict) else None
        if (
            isinstance(runtime, dict)
            and runtime.get("provider")
            and runtime.get("model")
        ):
            return result

        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            return result
        entry, subentry = management_ui.entry_and_agent(hass, entry_id, subentry_id)
        try:
            agent = result.get("agent", {})
            load_errors = result.get("load_errors", [])
            failed_keys = {
                item.get("key") for item in load_errors if isinstance(item, dict)
            }
            rebuilt = management_setup_health.build_setup_health_facts(
                hass,
                entry,
                subentry,
                memory_available="memories" not in failed_keys,
                knowledge_source_count=int(agent.get("knowledge_source_count", 0)),
                knowledge_available="knowledge" not in failed_keys,
                is_admin=is_admin,
            )
        except Exception:
            rebuilt = dict(facts) if isinstance(facts, dict) else {}
            rebuilt["provider_runtime"] = {
                "client_loaded": getattr(entry, "runtime_data", None) is not None,
                "provider": str(
                    entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER)
                ),
                "model": str(
                    subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
                ).strip(),
            }
            rebuilt["can_manage"] = is_admin
            rebuilt["live_provider_tested"] = False
        return {**result, "setup_health": rebuilt}

    management_loading_performance.async_overview_summary = wrapped  # type: ignore[assignment]
    setattr(management_loading_performance, _OVERVIEW_PATCHED, True)


def install_management_function_quarantine() -> bool:
    """Install tolerant management seams without weakening strict config validation."""
    if getattr(management_ui, _PATCHED, False):
        return False

    management_ui.configured_function_tools_from_data = (  # type: ignore[assignment]
        _management_configured_tools
    )
    management_ui.merge_agent_config = _management_merge_agent_config  # type: ignore[assignment]
    # This module performs its Request Rule preflight before delegating to the
    # management dispatcher, so give that preflight the same valid-sibling view.
    function_dependency_integrity.configured_function_tools_from_data = (  # type: ignore[assignment]
        _dependency_configured_tools
    )
    management_ui.async_test_agent = _tolerant_agent_test  # type: ignore[assignment]
    management_ui.async_management_command = _wrap_management_command(  # type: ignore[assignment]
        management_ui.async_management_command
    )
    _install_overview_provider_fallback()
    setattr(management_ui, _PATCHED, True)
    return True
