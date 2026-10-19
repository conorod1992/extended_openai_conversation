"""Keep unrelated management surfaces usable while Function Tools need repair."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

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
from .const import CONF_FUNCTION_GROUPS, CONF_FUNCTION_TOOLS, DEFAULT_FUNCTION_GROUPS
from .live_subentry_updates import update_live_subentry
from .management_function_repair import (
    editable_function_tools,
    function_configuration_repair_state,
    function_tools_issue,
    isolated_function_tools,
    persist_valid_function_configuration,
    persisted_config_projection,
    repair_revision,
    repair_state_for_projection,
    safe_function_configuration as _safe_function_configuration,
)

_ALLOW_QUARANTINED_TOOLS: ContextVar[bool] = ContextVar(
    "extended_openai_management_allow_quarantined_tools", default=False
)
_QUARANTINED_FUNCTION_NAMES: ContextVar[frozenset[str]] = ContextVar(
    "extended_openai_management_quarantined_function_names", default=frozenset()
)


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


def _management_validate_function_groups(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ignore quarantined members without weakening normal group validation."""
    if not _ALLOW_QUARANTINED_TOOLS.get():
        return _STRICT_VALIDATE_FUNCTION_GROUPS(value, function_tools)
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


def _management_merge_agent_config(
    source: Any, updates: dict[str, Any]
) -> dict[str, Any]:
    """Validate unrelated section edits without rewriting repair-owned Function fields."""
    if not _ALLOW_QUARANTINED_TOOLS.get():
        return _STRICT_MERGE_AGENT_CONFIG(source, updates)

    raw = dict(source)
    state = function_configuration_repair_state(raw)
    if state is None or not is_field_only_configuration_update(updates):
        return _STRICT_MERGE_AGENT_CONFIG(source, updates)

    normalized = _STRICT_MERGE_AGENT_CONFIG(_safe_function_configuration(raw), updates)
    for key in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS):
        if key in raw:
            normalized[key] = deepcopy(raw[key])
        else:
            normalized.pop(key, None)
    return normalized


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


def _restore_quarantined_group_members(
    groups: list[dict[str, Any]],
    raw_groups: Any,
    quarantined: frozenset[str],
    renamed_group: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Retain hidden invalid members in persisted groups while editing valid siblings."""
    restored = deepcopy(groups)
    if not quarantined or not isinstance(raw_groups, list):
        return restored
    by_id = {
        group.get("id"): group
        for group in raw_groups
        if isinstance(group, dict)
        and isinstance(group.get("id"), str)
        and isinstance(group.get("functions"), list)
    }
    for group in restored:
        if not isinstance(group, dict) or not isinstance(group.get("functions"), list):
            continue
        original_id = group.get("id")
        if renamed_group is not None and original_id == renamed_group[1]:
            original_id = renamed_group[0]
        original = by_id.get(original_id)
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
    renamed_group: tuple[str, str] | None = None,
    deleted_group: str | None = None,
    renamed_function: tuple[str, str] | None = None,
    repaired_group: str | None = None,
) -> dict[str, Any]:
    """Persist edits to valid siblings while retaining quarantined raw tools."""
    raw = dict(subentry.data)
    _valid, invalid, _issue = isolated_function_tools(raw)
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

    if not state.field_updates_allowed:
        raise HomeAssistantError(
            "Saved Function Tools cannot be isolated safely; repair the whole collection first"
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
    persisted_groups = deepcopy(groups)
    # Invalid Group siblings are repair-owned. A targeted replacement or deletion
    # consumes only that raw definition; unrelated damage survives unchanged.
    original_groups = raw.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)
    if (
        renamed_group is not None
        and renamed_group[0] != renamed_group[1]
        and isinstance(original_groups, list)
        and any(
            isinstance(group, dict) and group.get("id") == renamed_group[1]
            for group in original_groups
        )
    ):
        raise HomeAssistantError(
            "Function Group already exists; reload it before replacing it"
        )
    consumed = {deleted_group, repaired_group}
    baseline_by_id = {group["id"]: group for group in state.groups}
    proposed_by_id = {group["id"]: group for group in groups}
    removed_tools = {tool["spec"]["name"] for tool in state.valid} - valid_names
    assigned_groups = {
        name: group["id"] for group in groups for name in group["functions"]
    }
    previous_assignments = {
        renamed_function[1]
        if renamed_function and name == renamed_function[0]
        else name: renamed_group[1]
        if renamed_group and group["id"] == renamed_group[0]
        else group["id"]
        for group in state.groups
        for name in group["functions"]
    }
    # Existing assignments do not authorize editing quarantined siblings.
    # Compare identities after renames so metadata and ID edits cannot become moves.
    moved_assignments = {
        name: group_id
        for name, group_id in assigned_groups.items()
        if previous_assignments.get(name) != group_id
    }
    if renamed_function and renamed_function[1] in moved_assignments:
        moved_assignments[renamed_function[0]] = moved_assignments[renamed_function[1]]
    restored_groups = []
    restored_ids = set()
    if isinstance(original_groups, list):
        for raw_group in original_groups:
            group_id = raw_group.get("id") if isinstance(raw_group, dict) else None
            if isinstance(group_id, str) and group_id in consumed:
                continue
            baseline = (
                baseline_by_id.get(group_id) if isinstance(group_id, str) else None
            )
            targeted = renamed_group is not None and group_id == renamed_group[0]
            proposed_id = renamed_group[1] if targeted and renamed_group else group_id
            proposed = (
                proposed_by_id.get(proposed_id)
                if isinstance(proposed_id, str)
                else None
            )
            if targeted and proposed is not None and baseline is None:
                baseline = {"functions": [tool["spec"]["name"] for tool in state.valid]}
            if (
                isinstance(group_id, str)
                and sum(
                    isinstance(item, dict) and item.get("id") == group_id
                    for item in original_groups
                )
                > 1
            ):
                baseline = None
            restored = deepcopy(raw_group)
            if baseline is not None and proposed is not None:
                # Only the usable members were edited indirectly. Retain hidden
                # raw members and fields that the operation never presented.
                baseline_members = set(baseline["functions"])
                proposed_members = proposed["functions"]
                restored.update(
                    {
                        key: deepcopy(value)
                        for key, value in proposed.items()
                        if key != "functions"
                        and (targeted or value != baseline.get(key))
                    }
                )
                restored["functions"] = [
                    renamed_function[1]
                    if renamed_function and member == renamed_function[0]
                    else member
                    for member in (
                        raw_group.get("functions", [])
                        if isinstance(raw_group.get("functions"), list)
                        else []
                    )
                    if not isinstance(member, str)
                    or member not in baseline_members
                    or member in proposed_members
                    or (
                        renamed_function
                        and member == renamed_function[0]
                        and renamed_function[1] in proposed_members
                    )
                ]
                for member in proposed_members:
                    if member not in restored["functions"]:
                        restored["functions"].append(member)
            elif isinstance(restored, dict) and isinstance(
                restored.get("functions"), list
            ):
                # Even structurally quarantined Groups retain Tool rename/delete
                # semantics, without dropping unrelated unavailable references.
                restored["functions"] = [
                    renamed_function[1]
                    if renamed_function and member == renamed_function[0]
                    else member
                    for member in restored["functions"]
                    if (
                        not isinstance(member, str)
                        or member not in removed_tools
                        or (renamed_function and member == renamed_function[0])
                    )
                    and (
                        not isinstance(member, str)
                        or member not in moved_assignments
                        or moved_assignments[member] == group_id
                    )
                ]
            if isinstance(group_id, str):
                restored_ids.add(group_id)
            if targeted and isinstance(proposed_id, str):
                restored_ids.add(proposed_id)
            restored_groups.append(restored)
        persisted_groups = [
            item for item in persisted_groups if item["id"] not in restored_ids
        ]
        persisted_groups.extend(restored_groups)
    persisted = dict(raw)
    persisted[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        [*deepcopy(tools), *quarantined_raw],
        sort_keys=False,
        allow_unicode=True,
    )
    if deepcopy(tools) == state.valid:
        # Group-only edits must not reserialize untouched Tools.
        if CONF_FUNCTION_TOOLS in raw:
            persisted[CONF_FUNCTION_TOOLS] = raw[CONF_FUNCTION_TOOLS]
        else:
            persisted.pop(CONF_FUNCTION_TOOLS, None)
    persisted[CONF_FUNCTION_GROUPS] = (
        persisted_groups
        if isinstance(original_groups, list)
        else deepcopy(original_groups)
    )
    if extra_updates:
        persisted.update(deepcopy(extra_updates))
    next_state = function_configuration_repair_state(persisted)
    response_groups = (
        next_state.groups
        if next_state is not None
        else _STRICT_VALIDATE_FUNCTION_GROUPS(persisted[CONF_FUNCTION_GROUPS], tools)
    )
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
    update_live_subentry(hass, entry, subentry, data=persisted)
    return {
        "function_repair": metadata,
        "functions": deepcopy(tools),
        "function_groups": deepcopy(response_groups),
        "revision": repair_revision(subentry),
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


@contextmanager
def management_function_tools(section: str) -> Iterator[None]:
    """Limit tolerant reads to the three existing repair-aware Management sections."""
    if section not in {"request_rules", "guest_mode", "tools"}:
        yield
        return
    token = _ALLOW_QUARANTINED_TOOLS.set(True)
    names_token = _QUARANTINED_FUNCTION_NAMES.set(frozenset())
    try:
        yield
    finally:
        _QUARANTINED_FUNCTION_NAMES.reset(names_token)
        _ALLOW_QUARANTINED_TOOLS.reset(token)
