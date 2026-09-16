"""Recovery boundary for invalid persisted Function Tool configuration."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .agent_config import (
    agent_config_defaults,
    validate_function_groups,
    validate_function_tools,
)
from .const import CONF_FUNCTION_GROUPS, CONF_FUNCTION_TOOLS, DEFAULT_FUNCTION_GROUPS
from .performance import cached_configured_function_tools_from_data

_STALE_CONFIGURATION_ERROR = (
    "Agent configuration changed in another tab; reload before saving"
)


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


def isolated_function_tools(
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Return valid tools plus individually invalid persisted tools when isolatable."""
    editable = editable_function_tools(options)
    if not isinstance(editable, list):
        return [], [], "Saved Function Tools must be a YAML/JSON array"

    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for index, candidate in enumerate(editable):
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
                    "validation_error": str(err) or type(err).__name__,
                }
            )
        else:
            valid.append(validated)

    if invalid:
        return valid, invalid, invalid[0]["validation_error"]

    # Some failures (for example collection-level invariants) cannot safely be
    # attributed to one item. Keep the whole-field repair fallback for those.
    try:
        validate_function_tools(deepcopy(editable))
    except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
        return [], [], str(err) or type(err).__name__
    return valid, [], None


def function_tools_issue(
    options: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None]:
    """Return usable tools while isolating a persisted validation/parsing failure."""
    try:
        return cached_configured_function_tools_from_data(options), None
    except (HomeAssistantError, yaml.YAMLError, TypeError, ValueError) as err:
        valid, _invalid, isolated_issue = isolated_function_tools(options)
        return valid, isolated_issue or str(err) or type(err).__name__


def _safe_function_configuration(options: dict[str, Any]) -> dict[str, Any]:
    """Build a management-only configuration with invalid tools excluded."""
    valid, _invalid, _issue = isolated_function_tools(options)
    safe = dict(options)
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
    raw_groups = deepcopy(options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS))
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


def _revision_for_data(management_ui: Any, title: str, data: dict[str, Any]) -> str:
    """Hash raw persisted state without invoking strict agent normalization."""
    document = management_ui.canonical_json({"title": title, "config": data})
    return sha256(document.encode("utf-8")).hexdigest()


def repair_revision(management_ui: Any, subentry: Any) -> str:
    """Return the optimistic-concurrency revision for a broken agent config."""
    return _revision_for_data(management_ui, subentry.title, dict(subentry.data))


def require_repair_revision(management_ui: Any, subentry: Any, revision: Any) -> None:
    """Reject stale repair writes without validating the broken configuration."""
    if not isinstance(revision, str) or revision != repair_revision(
        management_ui, subentry
    ):
        raise HomeAssistantError(_STALE_CONFIGURATION_ERROR)


def _safe_configuration_payload(
    hass: HomeAssistant,
    management_ui: Any,
    management_loading_performance: Any,
    entry: Any,
    subentry: Any,
) -> dict[str, Any]:
    """Return the normal management payload with invalid Function Tools omitted."""
    safe = _safe_function_configuration(dict(subentry.data))
    config = management_loading_performance._snapshot_normalized_configuration(safe)
    defaults = management_loading_performance._snapshot_normalized_configuration(
        agent_config_defaults()
    )
    return {
        "title": subentry.title,
        "revision": repair_revision(management_ui, subentry),
        "config": config,
        "defaults": defaults,
        "options": management_ui.agent_config_options(),
        "model_capabilities": management_ui.model_capabilities(
            config[management_ui.CONF_CHAT_MODEL]
        ),
        "function_types": sorted(management_ui.FUNCTIONS),
        "local_handling": management_ui.local_handling_snapshot(
            hass,
            str(entry.entry_id),
            str(subentry.subentry_id),
            config.get("local_intent_exclusions", []),
        ),
    }


def _function_fields_unchanged(
    updates: dict[str, Any], safe_config: dict[str, Any]
) -> bool:
    """Return whether a normal config save left repair-owned fields untouched."""
    for key in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS):
        if key in updates and updates[key] != safe_config.get(key):
            return False
    return True


async def async_function_repair(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Expose repair plus a tolerant management seam for invalid Function Tools."""
    del user_id
    from . import management_loading_performance, management_ui

    management_ui._require_admin(is_admin)
    entry_id = message.get("entry_id")
    subentry_id = message.get("subentry_id")
    if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
        raise HomeAssistantError("entry_id and subentry_id are required")
    entry, subentry = management_ui.entry_and_agent(hass, entry_id, subentry_id)
    _configured, issue = function_tools_issue(dict(subentry.data))
    if issue is None:
        raise HomeAssistantError("Function Tools do not require repair")

    action = message.get("action")
    if action == "configuration_get":
        return _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )

    if action == "configuration_validate":
        updates = message.get("config", {})
        if not isinstance(updates, dict):
            raise HomeAssistantError("config must be an object")
        safe_payload = _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )
        if not _function_fields_unchanged(updates, safe_payload["config"]):
            return {
                "valid": False,
                "errors": {
                    CONF_FUNCTION_TOOLS: "Repair invalid Function Tools before editing Function Tools or Function Groups"
                },
            }
        safe_base = _safe_function_configuration(dict(subentry.data))
        filtered = {
            key: value
            for key, value in updates.items()
            if key not in {CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS}
        }
        result = management_ui._validation_result(
            lambda: management_ui.agent_config_snapshot(
                management_ui.merge_agent_config(safe_base, filtered)
            )
        )
        if result.get("valid"):
            result["model_capabilities"] = management_ui.model_capabilities(
                result["config"][management_ui.CONF_CHAT_MODEL]
            )
        return result

    if action == "configuration_save":
        updates = message.get("config", {})
        if not isinstance(updates, dict):
            raise HomeAssistantError("config must be an object")
        safe_payload = _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )
        if not _function_fields_unchanged(updates, safe_payload["config"]):
            return {
                "valid": False,
                "errors": {
                    CONF_FUNCTION_TOOLS: "Repair invalid Function Tools before editing Function Tools or Function Groups"
                },
            }
        filtered = {
            key: value
            for key, value in updates.items()
            if key not in {CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS}
        }
        safe_base = _safe_function_configuration(dict(subentry.data))
        validation = management_ui._validation_result(
            lambda: management_ui.merge_agent_config(safe_base, filtered)
        )
        if not validation.get("valid"):
            return validation
        normalized = validation["config"]
        persisted = management_loading_performance.preserve_legacy_guest_policy(
            dict(subentry.data), deepcopy(normalized)
        )
        if CONF_FUNCTION_TOOLS in subentry.data:
            persisted[CONF_FUNCTION_TOOLS] = deepcopy(
                subentry.data[CONF_FUNCTION_TOOLS]
            )
        else:
            persisted.pop(CONF_FUNCTION_TOOLS, None)
        if CONF_FUNCTION_GROUPS in subentry.data:
            persisted[CONF_FUNCTION_GROUPS] = deepcopy(
                subentry.data[CONF_FUNCTION_GROUPS]
            )
        else:
            persisted.pop(CONF_FUNCTION_GROUPS, None)

        requested_title = message.get("title")
        if requested_title is not None and (
            not isinstance(requested_title, str) or not requested_title.strip()
        ):
            return {"valid": False, "errors": {"title": "must not be empty"}}
        saved_title = (
            requested_title.strip()
            if isinstance(requested_title, str)
            else subentry.title
        )
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data=persisted,
            **({"title": saved_title} if isinstance(requested_title, str) else {}),
        )
        safe_after = _safe_function_configuration(persisted)
        snapshot = management_loading_performance._snapshot_normalized_configuration(
            safe_after
        )
        return {
            "valid": True,
            "errors": {},
            "title": saved_title,
            "revision": _revision_for_data(management_ui, saved_title, persisted),
            "config": snapshot,
            "model_capabilities": management_ui.model_capabilities(
                snapshot[management_ui.CONF_CHAT_MODEL]
            ),
            "local_handling": management_ui.local_handling_snapshot(
                hass,
                str(entry.entry_id),
                str(subentry.subentry_id),
                snapshot.get("local_intent_exclusions", []),
            ),
            "agent": management_loading_performance._agent_snapshot(
                hass, entry, subentry, config=persisted, title=saved_title
            ),
        }

    if action == "get":
        _valid, invalid, _isolated_issue = isolated_function_tools(dict(subentry.data))
        return {
            "tools": editable_function_tools(dict(subentry.data)),
            "invalid_tools": invalid,
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
