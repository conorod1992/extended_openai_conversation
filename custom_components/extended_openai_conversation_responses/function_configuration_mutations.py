"""Prepare Function/Group edits with explicit caller policies.

Management owns revisions, awaited dependency checks and response projections.
Service toggles own only the requested field and retain their existing defaults.
Quarantine reconciliation preserves raw siblings that an edit did not present.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .agent_config import (
    merge_agent_config,
    project_agent_config_fields,
    project_agent_config_updates,
    validate_function_groups,
    validate_function_tools,
)
from .const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_FUNCTION_GROUPS,
)
from .function_configuration_repair import (
    FunctionConfigurationRepairState,
    editable_function_tools,
    function_configuration_repair_state,
)
from .yaml_serialization import dump_yaml


@dataclass(frozen=True, slots=True)
class PreparedFunctionConfiguration:
    """Scoped persisted data and its distinct usable response values."""

    persisted: dict[str, Any]
    tools: list[dict[str, Any]]
    groups: list[dict[str, Any]]
    repair_state: FunctionConfigurationRepairState | None = None


def prepare_valid_function_configuration(
    raw: Mapping[str, Any],
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    extra_updates: dict[str, Any] | None = None,
    validated_snapshot: dict[str, Any] | None = None,
) -> PreparedFunctionConfiguration:
    """Prepare a strict Management edit without materializing unrelated defaults.

    A validated snapshot may only come from the unchanged authoritative source.
    Unchanged Tools retain their exact persisted representation, including absence.
    """
    updates: dict[str, Any] = {
        CONF_FUNCTION_TOOLS: deepcopy(tools),
        CONF_FUNCTION_GROUPS: deepcopy(groups),
    }
    if extra_updates:
        updates.update(extra_updates)
    snapshot = validated_snapshot
    reuse_tools = (
        snapshot is not None
        and updates[CONF_FUNCTION_TOOLS] == snapshot[CONF_FUNCTION_TOOLS]
    )
    validated_functions = None
    if reuse_tools:
        assert snapshot is not None
        updates.pop(CONF_FUNCTION_TOOLS)
        validated_functions = (
            snapshot[CONF_FUNCTION_TOOLS],
            snapshot[CONF_FUNCTION_GROUPS]
            if updates[CONF_FUNCTION_GROUPS] == snapshot[CONF_FUNCTION_GROUPS]
            else None,
        )
    candidate = merge_agent_config(
        raw,
        updates,
        validated_functions=validated_functions,
        function_tools_as_list=True,
    )
    # Keep the final validated objects for the response. Only persistence needs
    # YAML; unchanged tools retain their exact stored representation.
    response_snapshot = deepcopy(candidate)
    if reuse_tools:
        if CONF_FUNCTION_TOOLS in raw:
            candidate[CONF_FUNCTION_TOOLS] = deepcopy(raw[CONF_FUNCTION_TOOLS])
        else:
            candidate.pop(CONF_FUNCTION_TOOLS, None)
    else:
        candidate[CONF_FUNCTION_TOOLS] = dump_yaml(
            candidate[CONF_FUNCTION_TOOLS], sort_keys=False
        )
    # Function edits must not turn implicit model-dependent defaults into saved
    # settings. For example, an absent effort follows each routed model's profile;
    # persisting the default model's effort can invalidate an unchanged route.
    changed_fields = {CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS, *(extra_updates or {})}
    normalized = project_agent_config_fields(raw, changed_fields, candidate)
    return PreparedFunctionConfiguration(
        normalized,
        response_snapshot[CONF_FUNCTION_TOOLS],
        response_snapshot[CONF_FUNCTION_GROUPS],
    )


def prepare_quarantined_function_configuration(
    raw: dict[str, Any],
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    state: FunctionConfigurationRepairState,
    *,
    extra_updates: dict[str, Any] | None = None,
    renamed_group: tuple[str, str] | None = None,
    deleted_group: str | None = None,
    renamed_function: tuple[str, str] | None = None,
    repaired_group: str | None = None,
) -> PreparedFunctionConfiguration:
    """Edit usable siblings while preserving unseen raw Tool/Group damage."""
    invalid = state.invalid
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
    persisted[CONF_FUNCTION_TOOLS] = dump_yaml(
        [*deepcopy(tools), *quarantined_raw],
        sort_keys=False,
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
        else validate_function_groups(persisted[CONF_FUNCTION_GROUPS], tools)
    )
    return PreparedFunctionConfiguration(
        persisted, deepcopy(tools), deepcopy(response_groups), next_state
    )


def _prepare_enabled(
    data: Mapping[str, Any], identifiers: list[str], enabled: bool, *, group: bool
) -> dict[str, Any]:
    raw = dict(data)
    state = function_configuration_repair_state(raw)
    if state is not None and state.field_updates_allowed:
        configured = deepcopy(state.valid)
    else:
        # Collection-wide damage is not an editable projection; keep strict errors.
        configured = validate_function_tools(
            raw.get(CONF_FUNCTION_TOOLS) or DEFAULT_CONF_FUNCTION_TOOLS
        )
    groups = []
    if state is not None:
        groups = deepcopy(state.groups)
    elif group:
        groups = validate_function_groups(raw.get(CONF_FUNCTION_GROUPS, []), configured)
    selected = groups if group else configured
    requested = list(dict.fromkeys(identifiers))
    available = {item["id"] if group else item["spec"]["name"] for item in selected}
    missing = [identifier for identifier in requested if identifier not in available]
    if missing:
        kind = "Function Group" if group else "Function Tool"
        raise HomeAssistantError(kind + " not found: " + ", ".join(sorted(missing)))
    for item in selected:
        if (item["id"] if group else item["spec"]["name"]) in requested:
            item["enabled"] = enabled
    if state is not None:
        return prepare_quarantined_function_configuration(
            raw, configured, groups, state
        ).persisted
    updates = {CONF_FUNCTION_GROUPS if group else CONF_FUNCTION_TOOLS: selected}
    normalized = merge_agent_config(raw, updates)
    return project_agent_config_updates(raw, updates, normalized)


def prepare_function_tools_enabled(
    raw: Mapping[str, Any], names: list[str], enabled: bool
) -> dict[str, Any]:
    """Change selected Tool flags using the service's existing defaults policy."""
    return _prepare_enabled(raw, names, enabled, group=False)


def prepare_function_groups_enabled(
    raw: Mapping[str, Any], group_ids: list[str], enabled: bool
) -> dict[str, Any]:
    """Change selected Group flags without owning the raw Tool field."""
    return _prepare_enabled(raw, group_ids, enabled, group=True)
