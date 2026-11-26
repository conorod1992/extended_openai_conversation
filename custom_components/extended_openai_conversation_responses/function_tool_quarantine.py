"""Request-local quarantine of invalid persisted Function Tools and group references."""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
import logging
from typing import Any

import yaml

from homeassistant.exceptions import HomeAssistantError

from .const import CONF_FUNCTION_TOOLS
from .function_configuration_repair import (
    isolated_function_tools as _isolated_function_tools,
)
from .operational_errors import failure_diagnostic
from .yaml_serialization import dump_yaml

_LOGGER = logging.getLogger(__name__)

_RUNTIME_QUARANTINED_FUNCTION_NAMES: ContextVar[frozenset[str]] = ContextVar(
    "extended_openai_runtime_quarantined_function_names", default=frozenset()
)
_RUNTIME_QUARANTINE_ALL_FUNCTIONS: ContextVar[bool] = ContextVar(
    "extended_openai_runtime_quarantine_all_functions", default=False
)


def _runtime_configured_function_tools(data: Any) -> list[dict[str, Any]]:
    """Return valid runtime tools while quarantining persisted invalid siblings."""
    from .agent_config import configured_function_tools_from_data

    try:
        tools = configured_function_tools_from_data(data)
    except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
        valid, invalid, issue = _isolated_function_tools(dict(data))
        if issue is None:
            raise
        quarantine_all = not invalid
        quarantined_names = frozenset(
            str(item["name"])
            for item in invalid
            if isinstance(item.get("name"), str) and item["name"]
        )
        _RUNTIME_QUARANTINED_FUNCTION_NAMES.set(quarantined_names)
        _RUNTIME_QUARANTINE_ALL_FUNCTIONS.set(quarantine_all)
        safe = dict(data)
        safe[CONF_FUNCTION_TOOLS] = dump_yaml(valid, sort_keys=False)
        tools = configured_function_tools_from_data(safe)
        # Parser/validation messages may echo private YAML values or templates.
        # The management repair view already exposes details explicitly.
        detail = f"field={CONF_FUNCTION_TOOLS} {failure_diagnostic(err).summary()}"
        if quarantine_all:
            _LOGGER.warning(
                "All configured Function Tools are invalid and were disabled for this "
                "request. Open Extended OpenAI > Functions to repair or remove the "
                "invalid configuration. Reason: %s",
                detail,
            )
        else:
            _LOGGER.warning(
                "Invalid Function Tool(s) %s were disabled for this request. Open "
                "Extended OpenAI > Functions to repair or remove them. Reason: %s",
                ", ".join(sorted(quarantined_names)) or "unnamed tool",
                detail,
            )
        return tools

    _RUNTIME_QUARANTINED_FUNCTION_NAMES.set(frozenset())
    _RUNTIME_QUARANTINE_ALL_FUNCTIONS.set(False)
    return tools


def _runtime_validate_function_groups(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ignore only group references to Function Tools quarantined for this request."""
    from .agent_config import validate_function_groups

    quarantined_names = _RUNTIME_QUARANTINED_FUNCTION_NAMES.get()
    quarantine_all = _RUNTIME_QUARANTINE_ALL_FUNCTIONS.get()
    safe = deepcopy(value) if quarantine_all or quarantined_names else value
    if (quarantine_all or quarantined_names) and isinstance(safe, list):
        for group in safe:
            if not isinstance(group, dict):
                continue
            functions = group.get("functions")
            if not isinstance(functions, list):
                continue
            if quarantine_all:
                group["functions"] = [
                    name for name in functions if not isinstance(name, str)
                ]
            else:
                group["functions"] = [
                    name
                    for name in functions
                    if not isinstance(name, str) or name not in quarantined_names
                ]
    try:
        return validate_function_groups(safe, function_tools)
    except (HomeAssistantError, TypeError, ValueError) as err:
        _LOGGER.warning(
            "Invalid Function Groups were quarantined (%s)", type(err).__name__
        )
        return _safe_group_memberships(safe, function_tools)


def _safe_group_memberships(
    value: Any, function_tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Retain healthy groups and block any tool whose membership is ambiguous."""
    from .agent_config import MAX_FUNCTION_GROUPS, validate_function_groups

    names = {tool["spec"]["name"] for tool in function_tools}
    valid_groups: list[dict[str, Any]] = []
    if not isinstance(value, list) or len(value) > MAX_FUNCTION_GROUPS:
        blocked = names
    else:
        blocked = set()
        for group in value:
            try:
                valid_groups.extend(validate_function_groups([group], function_tools))
            except HomeAssistantError, TypeError, ValueError:
                members = group.get("functions") if isinstance(group, dict) else None
                if not isinstance(members, list):
                    blocked = names
                    break
                blocked.update(member for member in members if isinstance(member, str))
        else:
            try:
                valid_groups = validate_function_groups(valid_groups, function_tools)
            except HomeAssistantError, TypeError, ValueError:
                # Collection-wide identity/membership conflicts cannot safely be
                # attributed to a single group. Preserve only independent tools.
                blocked.update(
                    name for group in valid_groups for name in group["functions"]
                )
                valid_groups = []
    blocked &= names
    if blocked:
        for group in valid_groups:
            group["functions"] = [
                name for name in group["functions"] if name not in blocked
            ]
        valid_groups.append(
            {
                "id": "",
                "name": "",
                "description": "",
                "enabled": False,
                "loading_mode": "on_demand",
                "functions": sorted(blocked),
            }
        )
    return valid_groups
