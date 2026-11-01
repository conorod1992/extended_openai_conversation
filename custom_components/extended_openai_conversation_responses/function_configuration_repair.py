"""Read-only analysis of persisted Function Tools and Groups.

Usable projections are for inspection and validation, never lossless persistence.
Raw source values remain authoritative; strict execution still validates every
selected Tool. This module owns no management commands, revisions or writes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
from typing import Any

import yaml

from homeassistant.exceptions import HomeAssistantError

from .agent_config import (
    NATIVE_FUNCTION_IMPLEMENTATIONS,
    configured_function_tools_from_data,
    validate_function_groups,
    validate_function_tools,
)
from .const import CONF_FUNCTION_GROUPS, CONF_FUNCTION_TOOLS, DEFAULT_FUNCTION_GROUPS
from .function_yaml import contains_yaml_types
from .request import canonical_json
from .yaml_serialization import dump_yaml, load_yaml_or_json


@dataclass
class FunctionConfigurationRepairState:
    """Validated quarantine data for exact persisted Functions and Groups."""

    valid: list[dict[str, Any]]
    invalid: list[dict[str, Any]]
    issue: str
    groups: list[dict[str, Any]]
    group_issues: list[dict[str, Any]]
    raw_groups: Any
    field_updates_allowed: bool = True


def editable_function_tools(options: dict[str, Any]) -> Any:
    """Return persisted Function Tools without applying the current strict schema."""
    raw = options.get(CONF_FUNCTION_TOOLS)
    if raw is None:
        return []
    if not isinstance(raw, str):
        return deepcopy(raw)
    try:
        parsed = load_yaml_or_json(raw)
    except yaml.YAMLError:
        return raw
    return [] if parsed is None else parsed


def _function_tools_cache_key(options: dict[str, Any]) -> tuple[str, str]:
    """Return a deterministic key for one persisted Function Tool revision."""
    raw = options.get(CONF_FUNCTION_TOOLS)
    if raw is None:
        return ("none", "")
    if isinstance(raw, str):
        return ("string", raw)
    if contains_yaml_types(raw):
        return ("yaml", dump_yaml(raw, sort_keys=True))
    try:
        return ("json", canonical_json(raw))
    except TypeError, ValueError:
        return ("yaml", dump_yaml(raw, sort_keys=True))


def _options_from_function_tools_cache_key(kind: str, payload: str) -> dict[str, Any]:
    if kind == "none":
        return {}
    if kind == "string":
        return {CONF_FUNCTION_TOOLS: payload}
    if kind == "json":
        return {CONF_FUNCTION_TOOLS: json.loads(payload)}
    return {CONF_FUNCTION_TOOLS: yaml.safe_load(payload)}


def _isolate_function_tools_uncached(
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Compute tolerant Function Tool state for one persisted revision."""
    editable = editable_function_tools(options)
    if not isinstance(editable, list):
        return [], [], "Saved Function Tools must be a YAML/JSON array"

    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    saved_names: set[str] = set()
    for index, candidate in enumerate(editable):
        spec = candidate.get("spec") if isinstance(candidate, dict) else None
        name = spec.get("name") if isinstance(spec, dict) else None
        if isinstance(name, str) and name:
            if name in saved_names:
                # References identify Tools by name, including quarantined ones.
                # Indexed repair cannot safely rename/delete an ambiguous name.
                return [], [], f"duplicate tool name: {name}"
            saved_names.add(name)
        try:
            validated = validate_function_tools([deepcopy(candidate)])[0]
        except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
            name = None
            if isinstance(candidate, dict):
                spec = candidate.get("spec")
                if isinstance(spec, dict) and isinstance(spec.get("name"), str):
                    name = spec["name"]
            invalid.append(
                {
                    "index": index,
                    "name": name,
                    "tool": deepcopy(candidate),
                    "yaml": dump_yaml(candidate, sort_keys=False),
                    "validation_error": str(err) or type(err).__name__,
                }
            )
        else:
            valid.append(validated)

    if invalid:
        try:
            if len(valid) > 1:
                valid = validate_function_tools(deepcopy(valid))
        except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
            # Individually valid siblings can still conflict as a collection.
            # Keep the whole-field repair fallback, rather than exposing an
            # ambiguous subset as independently editable Tools.
            return [], [], str(err) or type(err).__name__
        return valid, invalid, invalid[0]["validation_error"]

    # Some failures (for example collection-level invariants) cannot safely be
    # attributed to one item. Keep the whole-field repair fallback for those.
    try:
        validate_function_tools(deepcopy(editable))
    except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
        return [], [], str(err) or type(err).__name__
    return valid, [], None


@lru_cache(maxsize=128)
def _cached_isolated_function_tools(
    kind: str, payload: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    return _isolate_function_tools_uncached(
        _options_from_function_tools_cache_key(kind, payload)
    )


def isolated_function_tools(
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Return cached tolerant Function Tool state for one persisted revision."""
    valid, invalid, issue = _cached_isolated_function_tools(
        *_function_tools_cache_key(options)
    )
    return deepcopy(valid), deepcopy(invalid), issue


@lru_cache(maxsize=128)
def _cached_function_tool_state(
    kind: str, payload: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, int | None]:
    """Resolve one persisted Function Tool revision once for all Management reads."""
    options = _options_from_function_tools_cache_key(kind, payload)
    try:
        valid = configured_function_tools_from_data(options)
    except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
        editable = editable_function_tools(options)
        valid, invalid, isolated_issue = isolated_function_tools(options)
        issue = isolated_issue or str(err) or type(err).__name__
        total_count = len(editable) if isinstance(editable, list) else None
        return valid, invalid, issue, total_count
    return valid, [], None, len(valid)


def function_tools_issue(
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return cached usable tools plus any persisted validation/parsing failure."""
    valid, _invalid, issue, _total_count = _cached_function_tool_state(
        *_function_tools_cache_key(options)
    )
    return deepcopy(valid), issue


def effective_function_configuration(
    options: dict[str, Any],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    """Return a usable config while retaining metadata for quarantined siblings."""
    state = function_configuration_repair_state(options)
    safe = dict(options)
    valid = state.valid if state else function_tools_issue(options)[0]
    safe[CONF_FUNCTION_TOOLS] = dump_yaml(valid, sort_keys=False)
    safe[CONF_FUNCTION_GROUPS] = (
        state.groups
        if state
        else validate_function_groups(
            options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS), valid
        )
    )
    return (
        safe,
        state.invalid if state else [],
        state.group_issues if state else [],
        deepcopy(options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)),
        state.issue if state else None,
    )


def _effective_function_groups(
    options: dict[str, Any], valid: list[dict[str, Any]]
) -> tuple[Any, list[dict[str, Any]], Any]:
    """Remove unavailable members from the editor copy, preserving persisted groups."""
    valid_names = {
        tool["spec"]["name"]
        for tool in valid
        if isinstance(tool, dict)
        and isinstance(tool.get("spec"), dict)
        and isinstance(tool["spec"].get("name"), str)
    }
    raw_groups = deepcopy(options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS))
    effective_groups = deepcopy(raw_groups)
    group_issues: list[dict[str, Any]] = []
    if isinstance(effective_groups, list):
        for group in effective_groups:
            if not isinstance(group, dict) or not isinstance(
                group.get("functions"), list
            ):
                continue
            persisted_functions = list(group["functions"])
            unavailable = [
                name
                for name in persisted_functions
                if isinstance(name, str) and name not in valid_names
            ]
            if unavailable:
                group_issues.append(
                    {
                        "id": group.get("id"),
                        "name": group.get("name"),
                        "unavailable_functions": unavailable,
                    }
                )
            invalid_members = [
                name for name in persisted_functions if not isinstance(name, str)
            ]
            if invalid_members:
                group_issues.append(
                    {
                        "id": group.get("id"),
                        "name": group.get("name"),
                        "invalid_members": deepcopy(invalid_members),
                    }
                )
            group["functions"] = [
                name
                for name in persisted_functions
                if isinstance(name, str) and name in valid_names
            ]
    return effective_groups, group_issues, raw_groups


def has_unavailable_native_tool(options: dict[str, Any]) -> bool:
    """Cheap positive repair preflight for unavailable native implementations.

    A negative result does not certify validity; the strict snapshot still does.
    """
    raw = options.get(CONF_FUNCTION_TOOLS)
    if isinstance(raw, str) and "native" not in raw:
        return False
    editable = editable_function_tools(options)
    if not isinstance(editable, list):
        return False
    return any(
        isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and tool["function"].get("type") == "native"
        and tool["function"].get("name") not in NATIVE_FUNCTION_IMPLEMENTATIONS
        for tool in editable
    )


def function_configuration_repair_state(
    options: dict[str, Any],
) -> FunctionConfigurationRepairState | None:
    """Share Tool and Group recovery policy across editors and backups.

    Unrelated writes require an isolatable Tool collection; unreadable YAML or
    collection-wide errors still require explicit repair. Recovery snapshots may
    retain opaque input for repair without certifying it for an ordinary write.
    """
    valid, issue = function_tools_issue(options)
    invalid: list[dict[str, Any]] = []
    if issue is not None:
        valid, invalid, issue = isolated_function_tools(options)
    field_updates_allowed = issue is None or bool(invalid)
    groups, group_issues, raw_groups = _effective_function_groups(options, valid)
    try:
        groups = validate_function_groups(groups, valid)
    except (HomeAssistantError, TypeError, ValueError) as err:
        # A malformed saved group is repair-owned data too. Exclude it from
        # this validation/display projection without rewriting the raw field.
        usable: list[dict[str, Any]] = []
        if isinstance(groups, list):
            for index, group in enumerate(groups):
                try:
                    usable = validate_function_groups([*usable, group], valid)
                except (HomeAssistantError, TypeError, ValueError) as group_error:
                    group_issues.append(
                        {"index": index, "validation_error": str(group_error)}
                    )
        groups = usable
        group_issues.append({"validation_error": str(err)})
        issue = issue or str(err)
    if issue is None and not group_issues:
        return None
    issue = issue or "Function Groups contain unavailable members"
    return FunctionConfigurationRepairState(
        valid, invalid, issue, groups, group_issues, raw_groups, field_updates_allowed
    )


def safe_function_configuration(options: dict[str, Any]) -> dict[str, Any]:
    """Build a usable validation projection; never use it as lossless source data."""
    safe, _invalid, _group_issues, _raw_groups, _issue = (
        effective_function_configuration(options)
    )
    return safe
