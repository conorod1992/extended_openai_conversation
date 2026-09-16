"""Keep unrelated management surfaces usable while Function Tools need repair."""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

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
from .management_function_repair import (
    editable_function_tools,
    effective_function_configuration,
    function_tools_issue,
    isolated_function_tools,
    repair_revision,
)

_PATCHED = "extended_openai_management_function_quarantine"
_OVERVIEW_PATCHED = "extended_openai_management_function_quarantine_overview"
_ALLOW_QUARANTINED_TOOLS: ContextVar[bool] = ContextVar(
    "extended_openai_management_allow_quarantined_tools", default=False
)
_QUARANTINED_FUNCTION_NAMES: ContextVar[frozenset[str]] = ContextVar(
    "extended_openai_management_quarantined_function_names", default=frozenset()
)

# This module is imported before management_ui registers static paths, so keep the
# UX helpers introduced alongside quarantine available to Home Assistant as served
# frontend assets as well as management-bootstrap dependencies.
management_ui.MANAGEMENT_FRONTEND_MODULES = tuple(  # type: ignore[assignment]
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
_STRICT_VALIDATE_FUNCTION_GROUPS = management_ui.validate_function_groups
_STRICT_PERSIST_FUNCTION_CONFIGURATION = management_ui._persist_function_configuration
_STRICT_AGENT_CONFIG_REVISION = management_ui._agent_config_revision
_ORIGINAL_AGENT_TEST = management_ui.async_test_agent


def _safe_function_configuration(data: dict[str, Any]) -> dict[str, Any]:
    """Return a management-only copy with invalid Function Tools excluded."""
    safe, _invalid, _group_issues, _raw_groups, _issue = (
        effective_function_configuration(data)
    )
    return safe


def _usable_function_tools(data: Any) -> list[dict[str, Any]]:
    """Return valid siblings when persisted Function Tools contain repairable errors."""
    raw = dict(data)
    tools, issue = function_tools_issue(raw)
    if issue is not None:
        _valid, invalid, _isolated_issue = isolated_function_tools(raw)
        _QUARANTINED_FUNCTION_NAMES.set(
            frozenset(
                str(item["name"])
                for item in invalid
                if isinstance(item.get("name"), str) and item["name"]
            )
        )
        return tools
    _QUARANTINED_FUNCTION_NAMES.set(frozenset())
    return _STRICT_CONFIGURED_TOOLS(data)


def _management_configured_tools(data: Any) -> list[dict[str, Any]]:
    """Use tolerant Function Tool loading only inside isolated management sections."""
    if _ALLOW_QUARANTINED_TOOLS.get():
        return _usable_function_tools(data)
    return _STRICT_CONFIGURED_TOOLS(data)


def _dependency_configured_tools(data: Any) -> list[dict[str, Any]]:
    """Validate Request Rule references against usable siblings, not broken tools."""
    return _usable_function_tools(data)


def _management_validate_function_groups(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ignore quarantined members without weakening normal group validation."""
    if not _ALLOW_QUARANTINED_TOOLS.get():
        return _STRICT_VALIDATE_FUNCTION_GROUPS(value, function_tools)
    quarantined = _QUARANTINED_FUNCTION_NAMES.get()
    if not quarantined:
        return _STRICT_VALIDATE_FUNCTION_GROUPS(value, function_tools)
    safe = deepcopy(value)
    if isinstance(safe, list):
        for group in safe:
            if not isinstance(group, dict) or not isinstance(
                group.get("functions"), list
            ):
                continue
            group["functions"] = [
                name for name in group["functions"] if name not in quarantined
            ]
    return _STRICT_VALIDATE_FUNCTION_GROUPS(safe, function_tools)


def _management_agent_config_revision(data: Any, title: str) -> str:
    """Use a raw revision only when strict normalization is blocked by Function Tools."""
    try:
        return _STRICT_AGENT_CONFIG_REVISION(data, title)
    except HomeAssistantError, yaml.YAMLError, TypeError, ValueError:
        raw = dict(data)
        _tools, issue = function_tools_issue(raw)
        if issue is None:
            raise
        payload = management_ui.canonical_json({"title": title, "config": raw})
        return sha256(payload.encode("utf-8")).hexdigest()


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


def _restore_quarantined_group_members(
    groups: list[dict[str, Any]], raw_groups: Any, quarantined: frozenset[str]
) -> list[dict[str, Any]]:
    """Retain hidden invalid members in persisted groups while editing valid siblings."""
    restored = deepcopy(groups)
    if not quarantined or not isinstance(raw_groups, list):
        return restored
    by_id = {
        group.get("id"): group
        for group in raw_groups
        if isinstance(group, dict) and isinstance(group.get("functions"), list)
    }
    for group in restored:
        if not isinstance(group, dict) or not isinstance(group.get("functions"), list):
            continue
        original = by_id.get(group.get("id"))
        if not isinstance(original, dict):
            continue
        hidden = [
            name
            for name in original.get("functions", [])
            if isinstance(name, str) and name in quarantined
        ]
        for name in hidden:
            if name not in group["functions"]:
                group["functions"].append(name)
    return restored


def _tolerant_persist_function_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    extra_updates: dict[str, Any] | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Persist edits to valid siblings while retaining quarantined raw tools."""
    raw = dict(subentry.data)
    _valid, invalid, issue = isolated_function_tools(raw)
    if issue is None or not invalid:
        return _STRICT_PERSIST_FUNCTION_CONFIGURATION(
            hass,
            entry,
            subentry,
            tools,
            groups,
            extra_updates=extra_updates,
            expected_revision=expected_revision,
        )

    if expected_revision is not None and expected_revision != repair_revision(
        management_ui, subentry
    ):
        raise HomeAssistantError(
            "Configuration changed in another tab. Reload the latest saved settings before saving."
        )

    invalid_names = frozenset(
        str(item["name"])
        for item in invalid
        if isinstance(item.get("name"), str) and item["name"]
    )
    valid_names = {
        str(tool.get("spec", {}).get("name"))
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("spec"), dict)
    }
    duplicate = sorted(name for name in invalid_names if name in valid_names)
    if duplicate:
        raise HomeAssistantError(
            f"Function Tool {duplicate[0]} already exists as a quarantined tool"
        )

    editable = editable_function_tools(raw)
    if not isinstance(editable, list):
        raise HomeAssistantError("Saved Function Tools cannot be isolated safely")
    invalid_indices = {
        int(item["index"]) for item in invalid if isinstance(item.get("index"), int)
    }
    quarantined_raw = [
        deepcopy(candidate)
        for index, candidate in enumerate(editable)
        if index in invalid_indices
    ]
    persisted_groups = _restore_quarantined_group_members(
        groups,
        raw.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS),
        invalid_names,
    )
    persisted = dict(raw)
    persisted[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        [*deepcopy(tools), *quarantined_raw],
        sort_keys=False,
        allow_unicode=True,
    )
    persisted[CONF_FUNCTION_GROUPS] = persisted_groups
    if extra_updates:
        persisted.update(deepcopy(extra_updates))
    persisted = management_ui.preserve_legacy_guest_policy(raw, persisted)
    hass.config_entries.async_update_subentry(entry, subentry, data=persisted)
    return {
        "functions": deepcopy(tools),
        "function_groups": deepcopy(groups),
        "revision": repair_revision(management_ui, subentry),
    }


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
    """Scope tolerant Function Tool reads to management surfaces that can degrade."""

    @wraps(original)
    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        if message.get("section") not in {"request_rules", "guest_mode", "tools"}:
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )
        token = _ALLOW_QUARANTINED_TOOLS.set(True)
        names_token = _QUARANTINED_FUNCTION_NAMES.set(frozenset())
        try:
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )
        finally:
            _QUARANTINED_FUNCTION_NAMES.reset(names_token)
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
    management_ui.validate_function_groups = _management_validate_function_groups  # type: ignore[assignment]
    management_ui.merge_agent_config = _management_merge_agent_config  # type: ignore[assignment]
    management_ui._agent_config_revision = _management_agent_config_revision  # type: ignore[assignment]
    management_ui._persist_function_configuration = (  # type: ignore[assignment]
        _tolerant_persist_function_configuration
    )
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
