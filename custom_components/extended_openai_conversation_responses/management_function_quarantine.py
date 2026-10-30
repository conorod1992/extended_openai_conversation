"""Keep unrelated management surfaces usable while Function Tools need repair."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import function_configuration_mutations as function_mutations
from .agent_config import (
    AgentConfigError,
    configured_function_tools_from_data as _STRICT_CONFIGURED_TOOLS,
    merge_agent_config as _STRICT_MERGE_AGENT_CONFIG,
    project_agent_config_updates,
    validate_function_groups as _STRICT_VALIDATE_FUNCTION_GROUPS,
)
from .agent_test import (
    AgentTestResult,
    TestCheck,
    _overall,
    async_test_agent as async_test_configured_agent,
)
from .const import CONF_FUNCTION_GROUPS, CONF_FUNCTION_TOOLS
from .function_configuration_repair import (
    function_configuration_repair_state,
    function_tools_issue,
    safe_function_configuration as _safe_function_configuration,
)
from .live_subentry_updates import update_live_subentry
from .management_function_repair import (
    persist_valid_function_configuration,
    persisted_config_projection,
    repair_revision,
    repair_state_for_projection,
)


def inspect_function_tools(data: Any) -> list[dict[str, Any]]:
    """Inspect valid siblings without certifying the persisted source for execution."""
    raw = dict(data)
    tools, issue = function_tools_issue(raw)
    if issue is not None:
        return tools
    return _STRICT_CONFIGURED_TOOLS(data)


def inspect_function_groups(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ignore quarantined members without weakening normal group validation."""
    try:
        return _STRICT_VALIDATE_FUNCTION_GROUPS(value, function_tools)
    except HomeAssistantError, TypeError, ValueError:
        pass
    state = function_configuration_repair_state(
        {
            # These tools are decoded: an empty usable collection is not the
            # historical falsy persisted value that means "load defaults".
            CONF_FUNCTION_TOOLS: deepcopy(function_tools) if function_tools else "[]",
            CONF_FUNCTION_GROUPS: value,
        }
    )
    return (
        state.groups
        if state
        else _STRICT_VALIDATE_FUNCTION_GROUPS(value, function_tools)
    )


def is_field_only_configuration_update(updates: Mapping[str, Any]) -> bool:
    """Submitted Function/Group fields always require their strict edit contract."""
    return not set(updates) & {CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS}


def merge_unchanged_function_configuration(
    subentry: Any, updates: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a scoped save payload and a separate normalized display candidate."""
    if not is_field_only_configuration_update(updates):
        raise HomeAssistantError(
            "Unchanged-function merge cannot change Functions or Groups"
        )
    projection = persisted_config_projection(subentry)
    snapshot = projection.snapshot
    repair_state = None
    # Field-only updates cannot activate or rewrite Functions/Groups. Their
    # validation projection must therefore work independently of the UI section
    # that initiated the edit; raw repair-owned fields are projected unchanged.
    if snapshot is None:
        repair_state, _ = repair_state_for_projection(projection)
    if repair_state is not None and not repair_state.field_updates_allowed:
        raise AgentConfigError(
            CONF_FUNCTION_TOOLS,
            "repair the malformed Function Tool collection before saving unrelated settings",
        )
    validated_functions = (
        (snapshot[CONF_FUNCTION_TOOLS], snapshot[CONF_FUNCTION_GROUPS])
        if snapshot is not None
        else (repair_state.valid, repair_state.groups)
        if repair_state is not None
        else None
    )
    candidate = _STRICT_MERGE_AGENT_CONFIG(
        subentry.data,
        updates,
        validated_functions=validated_functions,
        function_tools_as_list=True,
    )
    persisted = project_agent_config_updates(subentry.data, updates, candidate)
    return persisted, candidate


def _tolerant_persist_function_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    extra_updates: dict[str, Any] | None = None,
    expected_revision: str | None = None,
    renamed_group: tuple[str, str] | None = None,
    deleted_group: str | None = None,
    renamed_function: tuple[str, str] | None = None,
    repaired_group: str | None = None,
) -> dict[str, Any]:
    """Persist edits to valid siblings while retaining quarantined raw tools."""
    raw = dict(subentry.data)
    state = function_configuration_repair_state(raw)
    if state is None:
        return persist_valid_function_configuration(
            hass,
            entry,
            subentry,
            tools,
            groups,
            extra_updates=extra_updates,
            expected_revision=expected_revision,
        )

    if expected_revision is not None and expected_revision != repair_revision(subentry):
        raise HomeAssistantError(
            "Configuration changed in another tab. Reload the latest saved settings before saving."
        )

    prepared = function_mutations.prepare_quarantined_function_configuration(
        raw,
        tools,
        groups,
        state,
        extra_updates=extra_updates,
        renamed_group=renamed_group,
        deleted_group=deleted_group,
        renamed_function=renamed_function,
        repaired_group=repaired_group,
    )
    next_state = prepared.repair_state
    metadata = (
        None
        if next_state is None
        else {
            "invalid_tools": deepcopy(next_state.invalid),
            "invalid_count": len(next_state.invalid),
            "group_issues": deepcopy(next_state.group_issues),
            "persisted_groups": deepcopy(next_state.raw_groups),
            "validation_error": next_state.issue,
            "isolatable": bool(next_state.invalid),
            "tools_valid": not next_state.invalid and next_state.field_updates_allowed,
        }
    )
    update_live_subentry(hass, entry, subentry, data=prepared.persisted)
    revision = repair_revision(subentry)
    return {
        "function_repair": metadata,
        "functions": prepared.tools,
        "function_groups": prepared.groups,
        "revision": revision,
    }


async def _tolerant_agent_test(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> AgentTestResult:
    """Run provider diagnostics with valid Function Tool siblings only."""
    raw = dict(subentry.data)
    state = function_configuration_repair_state(raw)
    if state is None:
        return await async_test_configured_agent(hass, entry, subentry)
    invalid = state.invalid

    safe_subentry = SimpleNamespace(
        data=_safe_function_configuration(raw),
        subentry_id=subentry.subentry_id,
        title=getattr(subentry, "title", ""),
    )
    result = await async_test_configured_agent(hass, entry, cast(Any, safe_subentry))
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
    if invalid or not state.field_updates_allowed:
        result.checks.append(TestCheck("Function Tools", "Warning", detail))
    if state.group_issues:
        result.checks.append(
            TestCheck(
                "Function Groups",
                "Warning",
                "Unavailable Function Groups were quarantined for this test",
            )
        )
    result.status = _overall(result.checks)
    return result
