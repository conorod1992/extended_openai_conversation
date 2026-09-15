"""Recovery boundary for invalid persisted Function Tool configuration."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .agent_config import validate_function_groups, validate_function_tools
from .const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    DEFAULT_FUNCTION_GROUPS,
)
from .performance import cached_configured_function_tools_from_data

_STALE_CONFIGURATION_ERROR = (
    "Agent configuration changed in another tab; reload before saving"
)


def function_tools_issue(
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return configured tools or isolate a persisted validation/parsing failure."""
    try:
        return cached_configured_function_tools_from_data(options), None
    except (HomeAssistantError, yaml.YAMLError) as err:
        return [], str(err) or type(err).__name__


def editable_function_tools(options: dict[str, Any]) -> Any:
    """Return persisted Function Tools without applying the current strict schema."""
    raw = options.get(CONF_FUNCTION_TOOLS)
    if raw is None:
        return []
    if not isinstance(raw, str):
        return deepcopy(raw)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    return [] if parsed is None else parsed


def _revision_for_data(management_ui: Any, title: str, data: dict[str, Any]) -> str:
    """Hash raw persisted state without invoking strict agent normalization."""
    document = management_ui.canonical_json({"title": title, "config": data})
    return sha256(document.encode("utf-8")).hexdigest()


def repair_revision(management_ui: Any, subentry: Any) -> str:
    """Return the optimistic-concurrency revision for a broken agent config."""
    return _revision_for_data(management_ui, subentry.title, dict(subentry.data))


def require_repair_revision(
    management_ui: Any, subentry: Any, revision: Any
) -> None:
    """Reject stale repair writes without validating the broken configuration."""
    if not isinstance(revision, str) or revision != repair_revision(
        management_ui, subentry
    ):
        raise HomeAssistantError(_STALE_CONFIGURATION_ERROR)


async def async_function_repair(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Expose a narrow repair seam for invalid persisted Function Tools."""
    del user_id
    from . import management_loading_performance, management_ui

    management_ui._require_admin(is_admin)
    entry, subentry = management_ui.entry_and_agent(
        hass, message.get("entry_id"), message.get("subentry_id")
    )
    _configured, issue = function_tools_issue(dict(subentry.data))
    if issue is None:
        raise HomeAssistantError("Function Tools do not require repair")

    action = message.get("action")
    if action == "get":
        return {
            "tools": editable_function_tools(dict(subentry.data)),
            "validation_error": issue,
            "revision": repair_revision(management_ui, subentry),
        }
    if action != "save":
        raise HomeAssistantError(f"Unknown Function Tool repair action: {action}")

    require_repair_revision(management_ui, subentry, message.get("revision"))
    candidate = message.get("tools")
    if not isinstance(candidate, list):
        raise HomeAssistantError("tools must be a JSON array")
    validated = validate_function_tools(candidate)
    validate_function_groups(
        subentry.data.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS), validated
    )

    persisted = dict(subentry.data)
    persisted[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        validated, sort_keys=False, allow_unicode=True
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=persisted)
    return {
        "valid": True,
        "tools": deepcopy(validated),
        "revision": _revision_for_data(management_ui, subentry.title, persisted),
        "agent": management_loading_performance._agent_snapshot(
            hass, entry, subentry, config=persisted
        ),
    }
