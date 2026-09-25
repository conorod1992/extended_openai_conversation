"""Request-local quarantine of invalid persisted Function Tools and group references."""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
import logging
from typing import Any

import yaml

from homeassistant.exceptions import HomeAssistantError

from .const import CONF_FUNCTION_TOOLS
from .management_function_repair import (
    isolated_function_tools as _isolated_function_tools,
)

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
        safe[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
            valid, sort_keys=False, allow_unicode=True
        )
        tools = configured_function_tools_from_data(safe)
        detail = issue or str(err) or type(err).__name__
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
    if not quarantine_all and not quarantined_names:
        return validate_function_groups(value, function_tools)

    safe = deepcopy(value)
    if isinstance(safe, list):
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
    return validate_function_groups(safe, function_tools)
