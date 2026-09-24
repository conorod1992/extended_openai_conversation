"""Recovery boundary for invalid persisted Function Tool configuration."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
from typing import Any

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .agent_config import (
    agent_config_defaults,
    agent_config_snapshot,
    configured_function_tool_metadata_from_data,
    configured_function_tools_from_data,
    function_tool_enabled,
    merge_agent_config as _strict_merge_agent_config,
    preserve_legacy_guest_policy,
    validate_function_groups,
    validate_function_tools,
)
from .const import CONF_FUNCTION_GROUPS, CONF_FUNCTION_TOOLS, DEFAULT_FUNCTION_GROUPS
from .request import canonical_json

_STALE_CONFIGURATION_ERROR = (
    "Agent configuration changed in another tab; reload before saving"
)
_health_cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_HEALTH_CACHE_LIMIT = 128


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


def _function_tools_cache_key(options: dict[str, Any]) -> tuple[str, str]:
    """Return a deterministic key for one persisted Function Tool revision."""
    raw = options.get(CONF_FUNCTION_TOOLS)
    if raw is None:
        return ("none", "")
    if isinstance(raw, str):
        return ("string", raw)
    try:
        return ("json", canonical_json(raw))
    except TypeError, ValueError:
        return ("yaml", yaml.safe_dump(raw, sort_keys=True, allow_unicode=True))


def _options_from_function_tools_cache_key(kind: str, payload: str) -> dict[str, Any]:
    if kind == "none":
        return {}
    if kind == "string":
        return {CONF_FUNCTION_TOOLS: payload}
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
                    "yaml": yaml.safe_dump(
                        candidate, sort_keys=False, allow_unicode=True
                    ),
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


def peek_function_tool_health(options: dict[str, Any]) -> dict[str, Any] | None:
    """Read an existing health projection without parsing or validating tools."""
    key = _function_tools_cache_key(options)
    health = _health_cache.get(key)
    if health is not None:
        _health_cache.move_to_end(key)
    return deepcopy(health) if health is not None else None


def management_function_tool_health(options: dict[str, Any]) -> dict[str, Any]:
    """Resolve health once per persisted Function Tool value."""
    key = _function_tools_cache_key(options)
    cached = _health_cache.get(key)
    if cached is not None:
        _health_cache.move_to_end(key)
        return deepcopy(cached)
    health = _uncached_function_tool_health(options)
    _health_cache[key] = deepcopy(health)
    if len(_health_cache) > _HEALTH_CACHE_LIMIT:
        _health_cache.popitem(last=False)
    return health


def _uncached_function_tool_health(options: dict[str, Any]) -> dict[str, Any]:
    """Compute metadata, including tolerant failure state."""
    try:
        metadata = configured_function_tool_metadata_from_data(options)
    except HomeAssistantError, yaml.YAMLError, TypeError, ValueError:
        valid, invalid, issue, total_count = _cached_function_tool_state(
            *_function_tools_cache_key(options)
        )
        return {
            "usable_count": len(valid),
            "enabled_count": sum(function_tool_enabled(tool) for tool in valid),
            "invalid_count": len(invalid),
            "total_count": total_count,
            "isolatable": bool(invalid),
            "validation_error": issue,
            "invalid_names": [
                str(
                    item.get("name") or f"Function Tool {int(item.get('index', 0)) + 1}"
                )
                for item in invalid
            ],
        }
    return {
        **metadata,
        "invalid_count": 0,
        "isolatable": False,
        "validation_error": None,
        "invalid_names": [],
    }


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
    valid, invalid, issue = isolated_function_tools(options)
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
            group["functions"] = [
                name for name in persisted_functions if name in valid_names
            ]
    safe[CONF_FUNCTION_GROUPS] = effective_groups
    return safe, invalid, group_issues, raw_groups, issue


def safe_function_configuration(options: dict[str, Any]) -> dict[str, Any]:
    """Build a management-safe configuration with invalid tools excluded."""
    safe, _invalid, _group_issues, _raw_groups, _issue = (
        effective_function_configuration(options)
    )
    return safe


def agent_config_revision_from_snapshot(config: dict[str, Any], title: str) -> str:
    """Hash one already-normalized frontend configuration snapshot."""
    document = canonical_json({"title": title, "config": config})
    return sha256(document.encode("utf-8")).hexdigest()


@lru_cache(maxsize=128)
def _cached_agent_config_snapshot(raw_json: str) -> dict[str, Any]:
    """Normalize one persisted agent revision once."""
    return agent_config_snapshot(json.loads(raw_json))


def cached_agent_config_snapshot(data: Any) -> dict[str, Any]:
    """Return an isolated normalized snapshot for unchanged persisted state."""
    raw_json = canonical_json(dict(data))
    return deepcopy(_cached_agent_config_snapshot(raw_json))


def agent_config_revision(data: Any, title: str) -> str:
    """Hash normalized valid state or unchanged raw state while tools need repair."""
    raw = dict(data)
    if function_tools_issue(raw)[1] is not None:
        return agent_config_revision_from_snapshot(raw, title)
    return agent_config_revision_from_snapshot(cached_agent_config_snapshot(raw), title)


def repair_revision(subentry: Any) -> str:
    """Return the optimistic-concurrency revision for a broken agent config."""
    return agent_config_revision(subentry.data, subentry.title)


def require_agent_config_revision(subentry: Any, expected_revision: Any) -> None:
    """Reject a stale normal management writer before persistence."""
    if expected_revision is None:
        return
    if not isinstance(expected_revision, str):
        raise HomeAssistantError("revision must be a string")
    if expected_revision != agent_config_revision(subentry.data, subentry.title):
        raise HomeAssistantError(
            "Configuration changed in another tab. Reload the latest saved settings before saving."
        )


def require_repair_revision(subentry: Any, revision: Any) -> None:
    """Reject stale repair writes without validating the broken configuration."""
    if not isinstance(revision, str) or revision != repair_revision(subentry):
        raise HomeAssistantError(_STALE_CONFIGURATION_ERROR)


def persist_valid_function_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    extra_updates: dict[str, Any] | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Persist a fully valid Function Tool transition behind one revision boundary."""
    require_agent_config_revision(subentry, expected_revision)
    updates: dict[str, Any] = {
        CONF_FUNCTION_TOOLS: tools,
        CONF_FUNCTION_GROUPS: groups,
    }
    if extra_updates:
        updates.update(extra_updates)
    normalized = preserve_legacy_guest_policy(
        subentry.data,
        _strict_merge_agent_config(subentry.data, updates),
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=normalized)
    snapshot = agent_config_snapshot(normalized)
    return {
        "functions": snapshot[CONF_FUNCTION_TOOLS],
        "function_groups": snapshot[CONF_FUNCTION_GROUPS],
        "revision": agent_config_revision(normalized, subentry.title),
    }


def _safe_configuration_payload(
    hass: HomeAssistant,
    management_ui: Any,
    management_loading_performance: Any,
    entry: Any,
    subentry: Any,
) -> dict[str, Any]:
    """Return normal management data plus quarantined Function Tool metadata."""
    persisted = dict(subentry.data)
    safe, invalid, group_issues, raw_groups, issue = effective_function_configuration(
        persisted
    )
    config = management_loading_performance._snapshot_normalized_configuration(safe)
    defaults = management_loading_performance._snapshot_normalized_configuration(
        agent_config_defaults()
    )
    return {
        "title": subentry.title,
        "revision": repair_revision(subentry),
        "config": config,
        "defaults": defaults,
        "options": management_ui.agent_config_options(),
        "model_capabilities": management_ui.model_capabilities(
            config[management_ui.CONF_CHAT_MODEL]
        ),
        "function_types": sorted(management_ui.FUNCTIONS),
        "function_repair": {
            "invalid_tools": invalid,
            "invalid_count": len(invalid),
            "group_issues": group_issues,
            "persisted_groups": raw_groups,
            "validation_error": issue,
            "isolatable": bool(invalid),
        },
    }


def _function_fields_unchanged(
    updates: dict[str, Any], safe_config: dict[str, Any]
) -> bool:
    """Return whether a normal config save left repair-owned fields untouched."""
    for key in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS):
        if key in updates and updates[key] != safe_config.get(key):
            return False
    return True


def _persist_raw_tools(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[Any],
    groups: Any,
) -> dict[str, Any]:
    """Persist a partially invalid collection without normalizing untouched siblings."""
    persisted = dict(subentry.data)
    persisted[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        tools, sort_keys=False, allow_unicode=True
    )
    persisted[CONF_FUNCTION_GROUPS] = deepcopy(groups)
    hass.config_entries.async_update_subentry(entry, subentry, data=persisted)
    return persisted


def _replace_group_function_name(
    groups: Any, old_name: str | None, new_name: str
) -> Any:
    """Retain group assignment when a repaired Function Tool is renamed."""
    updated = deepcopy(groups)
    if old_name is None or old_name == new_name or not isinstance(updated, list):
        return updated
    for group in updated:
        if not isinstance(group, dict) or not isinstance(group.get("functions"), list):
            continue
        group["functions"] = [
            new_name if name == old_name else name for name in group["functions"]
        ]
    return updated


def _remove_group_function_name(groups: Any, name: str | None) -> Any:
    """Remove references when a quarantined Function Tool is explicitly deleted."""
    updated = deepcopy(groups)
    if name is None or not isinstance(updated, list):
        return updated
    for group in updated:
        if not isinstance(group, dict) or not isinstance(group.get("functions"), list):
            continue
        group["functions"] = [item for item in group["functions"] if item != name]
    return updated


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
        safe_base = safe_function_configuration(dict(subentry.data))
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
        if result["valid"]:
            result["model_capabilities"] = management_ui.model_capabilities(
                result["config"][management_ui.CONF_CHAT_MODEL]
            )
        return result

    if action == "configuration_save":
        if message.get("revision") is not None:
            require_repair_revision(subentry, message["revision"])
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
        safe_base = safe_function_configuration(dict(subentry.data))
        validation = management_ui._validation_result(
            lambda: management_ui.merge_agent_config(safe_base, filtered)
        )
        if not validation.get("valid"):
            return validation
        normalized = validation["config"]
        persisted = preserve_legacy_guest_policy(
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
        safe_after = safe_function_configuration(persisted)
        snapshot = management_loading_performance._snapshot_normalized_configuration(
            safe_after
        )
        response = _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )
        response.update(
            {
                "valid": True,
                "errors": {},
                "title": saved_title,
                "revision": agent_config_revision(persisted, saved_title),
                "config": snapshot,
                "agent": management_loading_performance._agent_snapshot(
                    hass, entry, subentry, config=persisted, title=saved_title
                ),
            }
        )
        return response

    if action == "get":
        payload = _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )
        repair = payload["function_repair"]
        return {
            "tools": editable_function_tools(dict(subentry.data)),
            "invalid_tools": repair["invalid_tools"],
            "group_issues": repair["group_issues"],
            "validation_error": issue,
            "revision": payload["revision"],
        }

    if action in {"save_one", "delete_one"}:
        require_repair_revision(subentry, message.get("revision"))
        editable = editable_function_tools(dict(subentry.data))
        index = message.get("index")
        if not isinstance(editable, list) or not isinstance(index, int):
            raise HomeAssistantError("A valid Function Tool index is required")
        if index < 0 or index >= len(editable):
            raise HomeAssistantError(
                "The Function Tool changed position; reload and try again"
            )
        current = editable[index]
        old_name = None
        if isinstance(current, dict) and isinstance(current.get("spec"), dict):
            candidate_name = current["spec"].get("name")
            if isinstance(candidate_name, str):
                old_name = candidate_name
        groups = subentry.data.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)

        if action == "delete_one":
            editable.pop(index)
            persisted = _persist_raw_tools(
                hass,
                entry,
                subentry,
                editable,
                _remove_group_function_name(groups, old_name),
            )
        else:
            candidate = message.get("tool")
            if not isinstance(candidate, dict):
                raise HomeAssistantError("tool must be an object")
            validated_tool = validate_function_tools([candidate])[0]
            new_name = validated_tool["spec"]["name"]
            for sibling_index, sibling in enumerate(editable):
                if sibling_index == index or not isinstance(sibling, dict):
                    continue
                spec = sibling.get("spec")
                if isinstance(spec, dict) and spec.get("name") == new_name:
                    raise HomeAssistantError(f"Function Tool {new_name} already exists")
            editable[index] = validated_tool
            persisted = _persist_raw_tools(
                hass,
                entry,
                subentry,
                editable,
                _replace_group_function_name(groups, old_name, new_name),
            )

        payload = _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )
        payload["agent"] = management_loading_performance._agent_snapshot(
            hass, entry, subentry, config=persisted
        )
        return payload

    if action != "save":
        raise HomeAssistantError(f"Unknown Function Tool repair action: {action}")

    require_repair_revision(subentry, message.get("revision"))
    candidate = message.get("tools")
    if not isinstance(candidate, list):
        raise HomeAssistantError("tools must be a JSON array")
    validated_tools = validate_function_tools(candidate)
    validate_function_groups(
        subentry.data.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS),
        validated_tools,
    )

    persisted = dict(subentry.data)
    persisted[CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        validated_tools, sort_keys=False, allow_unicode=True
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=persisted)
    return {
        "valid": True,
        "tools": deepcopy(validated_tools),
        "revision": agent_config_revision(persisted, subentry.title),
        "agent": management_loading_performance._agent_snapshot(
            hass, entry, subentry, config=persisted
        ),
    }
