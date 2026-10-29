"""Recovery boundary for invalid persisted Function Tool configuration."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import sha256
import logging
from time import perf_counter
from typing import Any
from weakref import ReferenceType, ref

import yaml

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import function_configuration_mutations as function_mutations
from .agent_config import (
    AgentConfigError,
    agent_config_defaults,
    agent_config_snapshot,
    agent_retention_snapshot,
    configured_function_tool_metadata_from_data,
    function_tool_enabled,
    parse_configured_function_tools,
    project_agent_config_updates,
    validate_function_groups,
    validate_function_tools,
)
from .const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_FUNCTION_GROUPS,
)
from .function_configuration_repair import (
    FunctionConfigurationRepairState,
    _cached_function_tool_state,
    _function_tools_cache_key,
    editable_function_tools,
    effective_function_configuration,
    function_configuration_repair_state,
    function_tools_issue,
    has_unavailable_native_tool,
    safe_function_configuration,
)
from .live_subentry_updates import update_configuration_subentry, update_live_subentry
from .model_catalog import catalog_generation
from .request import canonical_json
from .yaml_serialization import dump_yaml

_LOGGER = logging.getLogger(__name__)

_STALE_CONFIGURATION_ERROR = (
    "Agent configuration changed in another tab; reload before saving"
)
_health_cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_HEALTH_CACHE_LIMIT = 128
_PROJECTION_CACHE_LIMIT = 128
_RETENTION_FIELDS = (CONF_USAGE_REQUEST_RETENTION_DAYS, CONF_USAGE_RUN_RETENTION_DAYS)
_MISSING = object()


@dataclass
class _PersistedProjection:
    subentry: Any
    data: Any
    title: str
    snapshot: dict[str, Any] | None
    revision: str
    retention: dict[str, Any]
    preparsed_function_tools: Any = field(default_factory=lambda: _MISSING)
    repair_state: FunctionConfigurationRepairState | None = None
    repair_snapshot: dict[str, Any] | None = None
    snapshot_generation: int = field(default_factory=catalog_generation)


def _refresh_catalog_snapshots(projection: _PersistedProjection) -> None:
    """Expire derived values without changing persisted revision or repair state."""
    generation = catalog_generation()
    if projection.snapshot_generation != generation:
        projection.snapshot = None
        projection.repair_snapshot = None
        projection.snapshot_generation = generation


_persisted_projections: OrderedDict[int, _PersistedProjection] = OrderedDict()
_revision_lineages: dict[int, tuple[ReferenceType[Any], Any, str, str]] = {}


def _remember_revision_lineage(
    subentry: Any, data: Any, title: str, revision: str
) -> None:
    """Retain the last generation while a live HA subentry exists."""
    key = id(subentry)

    def forget(dead: ReferenceType[Any]) -> None:
        current = _revision_lineages.get(key)
        if current is not None and current[0] is dead:
            del _revision_lineages[key]

    try:
        owner = ref(subentry, forget)
    except TypeError:
        # Small unit-test namespaces are not weak-referenceable. Their bounded
        # projection cache still tracks replacement within each test.
        return
    _revision_lineages[key] = (owner, data, title, revision)


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


def repair_state_for_projection(
    projection: _PersistedProjection,
) -> tuple[FunctionConfigurationRepairState | None, bool]:
    """Resolve quarantine once for the current persisted Function fields."""
    if projection.repair_state is not None:
        return projection.repair_state, True
    state = function_configuration_repair_state(dict(projection.data))
    projection.repair_state = state
    return state, False


def agent_config_revision_from_snapshot(config: dict[str, Any], title: str) -> str:
    """Hash authoritative persisted configuration and title for all writers."""
    document = canonical_json({"title": title, "config": config})
    return sha256(document.encode("utf-8")).hexdigest()


def agent_config_revision(data: Any, title: str) -> str:
    """Hash persisted state without validating or normalizing unrelated fields."""
    return agent_config_revision_from_snapshot(dict(data), title)


def persisted_config_projection(
    subentry: Any, diagnostics: dict[str, Any] | None = None
) -> _PersistedProjection:
    """Reuse lightweight persisted reads while authoritative state is unchanged.

    Home Assistant replaces subentry data through async_update_subentry. Identity,
    owner, and title together cover updates, deletion/recreation, and title edits
    without serializing the whole configuration to look up a cache entry.
    """
    key = id(subentry)
    cached = _persisted_projections.get(key)
    if (
        cached is not None
        and cached.subentry is subentry
        and cached.data is subentry.data
        and cached.title == subentry.title
    ):
        if diagnostics is not None:
            diagnostics["projection_cache_hit"] = True
        _refresh_catalog_snapshots(cached)
        _persisted_projections.move_to_end(key)
        return cached

    if diagnostics is not None:
        diagnostics["projection_cache_hit"] = False
    data, title = subentry.data, subentry.title
    content_revision = agent_config_revision(data, title)
    # Content alone misses A -> B -> A. Keep the previous projection alive so
    # replacement of HA's authoritative data mapping is a new generation even
    # when the restored bytes equal the original bytes.
    lineage = _revision_lineages.get(key)
    previous = (
        lineage[3]
        if lineage is not None and lineage[0]() is subentry
        else cached.revision
        if cached is not None and cached.subentry is subentry
        else None
    )
    if (
        lineage is not None
        and lineage[0]() is subentry
        and lineage[1] is data
        and lineage[2] == title
    ):
        revision = lineage[3]
    else:
        revision = (
            sha256(f"{previous}:{content_revision}".encode()).hexdigest()
            if previous is not None
            else content_revision
        )
    retention = agent_retention_snapshot(subentry.data)
    projection = _PersistedProjection(
        subentry, subentry.data, subentry.title, None, revision, retention
    )
    if (
        cached is not None
        and cached.subentry is subentry
        and cached.repair_state is not None
        and all(
            cached.data.get(field, _MISSING) == subentry.data.get(field, _MISSING)
            for field in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS)
        )
    ):
        projection.repair_state = cached.repair_state
    _persisted_projections[key] = projection
    _remember_revision_lineage(subentry, subentry.data, subentry.title, revision)
    _persisted_projections.move_to_end(key)
    if len(_persisted_projections) > _PROJECTION_CACHE_LIMIT:
        _persisted_projections.popitem(last=False)
    return projection


def saved_agent_config_revision(subentry: Any, data: Any, title: str) -> str:
    """Return the authoritative post-save revision after HA replaces its data."""
    if subentry.title == title and dict(subentry.data) == dict(data):
        return persisted_config_projection(subentry).revision
    # Lightweight test doubles may record a save without applying it.
    return agent_config_revision(data, title)


def normalized_persisted_config_snapshot(
    projection: _PersistedProjection,
    diagnostics: dict[str, Any] | None = None,
    *,
    default_snapshot: dict[str, Any] | None = None,
    preparsed_function_tools: Any = _MISSING,
) -> tuple[dict[str, Any], bool]:
    """Lazily normalize a full read and isolate the returned frontend data."""
    started = perf_counter()
    _refresh_catalog_snapshots(projection)
    hit = projection.snapshot is not None
    if preparsed_function_tools is _MISSING:
        preparsed_function_tools = projection.preparsed_function_tools
    reused_defaults = False
    if projection.snapshot is None:
        # An exact default persisted mapping has already passed the same
        # normalization and Function Tool validation used to build defaults.
        # Keep every non-default or malformed mapping on the strict path.
        if (
            default_snapshot is not None
            and dict(projection.data) == agent_config_defaults()
        ):
            projection.snapshot = deepcopy(default_snapshot)
            reused_defaults = True
        else:
            source = dict(projection.data)
            if preparsed_function_tools is not _MISSING:
                source[CONF_FUNCTION_TOOLS] = preparsed_function_tools
                projection.snapshot = agent_config_snapshot(
                    source, preparsed_function_tools=True
                )
            else:
                projection.snapshot = agent_config_snapshot(source)
    if diagnostics is not None:
        diagnostics["default_snapshot_reused"] = reused_defaults
        diagnostics["snapshot_build_ms"] = round((perf_counter() - started) * 1000, 2)
    started = perf_counter()
    snapshot = deepcopy(projection.snapshot)
    if diagnostics is not None:
        diagnostics["snapshot_copy_ms"] = round((perf_counter() - started) * 1000, 2)
    return snapshot, hit


async def async_prepare_persisted_function_tools(
    hass: HomeAssistant, projection: _PersistedProjection
) -> dict[str, Any]:
    """Decode cold persisted YAML once in a worker; validate HA objects on-loop."""
    options = dict(projection.data)
    configured = options.get(CONF_FUNCTION_TOOLS)
    if (
        projection.snapshot is None
        and projection.repair_state is None
        and projection.preparsed_function_tools is _MISSING
        and isinstance(configured, str)
    ):
        try:
            parsed = await hass.async_add_executor_job(
                parse_configured_function_tools, options
            )
        except yaml.YAMLError:
            # Retain the authoritative repair validator's malformed-YAML report.
            parsed = configured
        if (
            projection.subentry.data is not projection.data
            or projection.subentry.title != projection.title
            or _persisted_projections.get(id(projection.subentry)) is not projection
        ):
            raise HomeAssistantError(_STALE_CONFIGURATION_ERROR)
        projection.preparsed_function_tools = parsed
    if projection.preparsed_function_tools is not _MISSING:
        options[CONF_FUNCTION_TOOLS] = projection.preparsed_function_tools
    return options


async def async_prewarm_persisted_config_projection(
    hass: HomeAssistant, entry: Any, subentry: Any
) -> None:
    """Prime normal reads using the same worker preparation as a cold GET."""
    projection = persisted_config_projection(subentry)
    if projection.snapshot is not None or projection.repair_state is not None:
        return
    try:
        options = await async_prepare_persisted_function_tools(hass, projection)
    except HomeAssistantError:
        if subentry.data is not projection.data or subentry.title != projection.title:
            return
        raise
    if (
        entry.state is not ConfigEntryState.LOADED
        or entry.subentries.get(subentry.subentry_id) is not subentry
    ):
        return
    health = peek_function_tool_health(dict(projection.data))
    if bool(health and health.get("validation_error")) or has_unavailable_native_tool(
        options
    ):
        state, _ = repair_state_for_projection(projection)
        if state is not None:
            return
    try:
        normalized_persisted_config_snapshot(projection)
    except AgentConfigError:
        state, _ = repair_state_for_projection(projection)
        if state is None:
            raise


def seed_persisted_config_projection(
    entry: Any, subentry: Any, snapshot: dict[str, Any], revision: str
) -> None:
    """Seed a successful save from its authoritative normalized response."""
    current = getattr(entry, "subentries", {}).get(subentry.subentry_id, subentry)
    key = id(current)
    previous = _persisted_projections.get(key)
    repair_state = None
    if (
        previous is not None
        and previous.subentry is current
        and previous.repair_state is not None
        and all(
            previous.data.get(field, _MISSING) == current.data.get(field, _MISSING)
            for field in (CONF_FUNCTION_TOOLS, CONF_FUNCTION_GROUPS)
        )
    ):
        repair_state = previous.repair_state
    projection = _PersistedProjection(
        current,
        current.data,
        current.title,
        None if repair_state is not None else deepcopy(snapshot),
        revision,
        agent_retention_snapshot(current.data),
        repair_state=repair_state,
        repair_snapshot=deepcopy(snapshot) if repair_state is not None else None,
    )
    _persisted_projections[key] = projection
    _remember_revision_lineage(current, current.data, current.title, revision)
    _persisted_projections.move_to_end(key)
    if len(_persisted_projections) > _PROJECTION_CACHE_LIMIT:
        _persisted_projections.popitem(last=False)


def discard_persisted_config_projection(subentry: Any) -> None:
    """Drop one agent projection when its integration entry unloads."""
    key = id(subentry)
    cached = _persisted_projections.get(key)
    if cached is not None and cached.subentry is subentry:
        del _persisted_projections[key]


def repair_revision(subentry: Any) -> str:
    """Return the optimistic-concurrency revision for a broken agent config."""
    return persisted_config_projection(subentry).revision


def require_agent_config_revision(subentry: Any, expected_revision: Any) -> None:
    """Reject a stale normal management writer before persistence."""
    if expected_revision is None:
        return
    if not isinstance(expected_revision, str):
        raise HomeAssistantError("revision must be a string")
    if expected_revision != persisted_config_projection(subentry).revision:
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
    started = perf_counter()
    require_agent_config_revision(subentry, expected_revision)
    revision_check_ms = (perf_counter() - started) * 1000
    phase = perf_counter()
    prepared = function_mutations.prepare_valid_function_configuration(
        subentry.data,
        tools,
        groups,
        extra_updates=extra_updates,
        validated_snapshot=persisted_config_projection(subentry).snapshot,
    )
    normalized = prepared.persisted
    normalization_ms = (perf_counter() - phase) * 1000
    from .management_ui import _validated_model_request

    _validated_model_request(normalized, entry.data)
    phase = perf_counter()
    from .management_loading_performance import _snapshot_normalized_configuration

    response_data = dict(normalized)
    response_data[CONF_FUNCTION_TOOLS] = prepared.tools
    response_data[CONF_FUNCTION_GROUPS] = prepared.groups
    snapshot = _snapshot_normalized_configuration(response_data, validated=True)
    response_tools, response_groups = (
        snapshot[CONF_FUNCTION_TOOLS],
        snapshot[CONF_FUNCTION_GROUPS],
    )
    projection_ms = (perf_counter() - phase) * 1000
    phase = perf_counter()
    update_live_subentry(hass, entry, subentry, data=normalized)
    subentry_update_ms = (perf_counter() - phase) * 1000
    phase = perf_counter()
    revision = saved_agent_config_revision(subentry, normalized, subentry.title)
    try:
        seed_persisted_config_projection(entry, subentry, snapshot, revision)
    except Exception:
        _LOGGER.warning(
            "Committed Function configuration could not seed its optional cache"
        )
    projection_ms += (perf_counter() - phase) * 1000
    return {
        "functions": response_tools,
        "function_groups": response_groups,
        "revision": revision,
        "_performance": {
            "revision_check_ms": round(revision_check_ms, 3),
            "normalization_ms": round(normalization_ms, 3),
            "subentry_update_ms": round(subentry_update_ms, 3),
            "post_save_projection_ms": round(projection_ms, 3),
            "live_runtime_update": True,
            "persistence_total_ms": round((perf_counter() - started) * 1000, 3),
        },
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
            "tools_valid": not invalid and function_tools_issue(persisted)[1] is None,
            "tools": editable_function_tools(persisted) if not invalid else None,
        },
    }


def safe_configuration_payload(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    projection: _PersistedProjection | None = None,
) -> dict[str, Any]:
    """Share the existing quarantine read with normal configuration callers."""
    from . import management_loading_performance, management_ui

    if projection is not None:
        _refresh_catalog_snapshots(projection)
        state, _cache_hit = repair_state_for_projection(projection)
        if state is None:
            raise HomeAssistantError("Function Tools do not require repair")
        if projection.repair_snapshot is None:
            safe = dict(projection.data)
            safe[CONF_FUNCTION_TOOLS] = state.valid
            safe[CONF_FUNCTION_GROUPS] = state.groups
            projection.repair_snapshot = (
                management_loading_performance._snapshot_normalized_configuration(
                    safe, validated=True
                )
            )
        config = deepcopy(projection.repair_snapshot)
        return {
            "title": projection.title,
            "revision": projection.revision,
            "config": config,
            "defaults": deepcopy(_cached_repair_defaults(catalog_generation())),
            "options": management_ui.agent_config_options(),
            "model_capabilities": management_ui._configuration_model_capabilities(
                config[management_ui.CONF_CHAT_MODEL]
            ),
            "function_types": sorted(management_ui.FUNCTIONS),
            "function_repair": {
                "invalid_tools": deepcopy(state.invalid),
                "invalid_count": len(state.invalid),
                "group_issues": deepcopy(state.group_issues),
                "persisted_groups": deepcopy(state.raw_groups),
                "validation_error": state.issue,
                "isolatable": bool(state.invalid),
                "tools_valid": not state.invalid and state.field_updates_allowed,
                "tools": editable_function_tools(dict(subentry.data))
                if not state.invalid
                else None,
            },
        }
    return _safe_configuration_payload(
        hass, management_ui, management_loading_performance, entry, subentry
    )


@lru_cache(maxsize=1)
def _cached_repair_defaults(generation: int = 0) -> dict[str, Any]:
    """Build the unchanged legacy repair defaults projection once."""
    from .management_loading_performance import _snapshot_normalized_configuration

    return _snapshot_normalized_configuration(agent_config_defaults())


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
    extra_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a partially invalid collection without normalizing untouched siblings."""
    persisted = dict(subentry.data)
    persisted[CONF_FUNCTION_TOOLS] = dump_yaml(tools, sort_keys=False)
    persisted[CONF_FUNCTION_GROUPS] = deepcopy(groups)
    if extra_updates:
        persisted.update(deepcopy(extra_updates))
    from .management_ui import _validated_model_request

    candidate_state = function_configuration_repair_state(persisted)
    if candidate_state is not None and not candidate_state.field_updates_allowed:
        # Recovery can retain individually invalid siblings, but must not commit
        # new collection conflicts that hide every usable Tool in the fallback.
        raise AgentConfigError(CONF_FUNCTION_TOOLS, candidate_state.issue)
    _validated_model_request(safe_function_configuration(persisted), entry.data)
    update_live_subentry(hass, entry, subentry, data=persisted)
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
    state = function_configuration_repair_state(dict(subentry.data))
    if state is None:
        raise HomeAssistantError("Function Tools do not require repair")
    issue = state.issue

    action = message.get("action")
    if action == "configuration_get":
        return _safe_configuration_payload(
            hass, management_ui, management_loading_performance, entry, subentry
        )

    if action in {"configuration_validate", "configuration_save"}:
        # Both routes admit the same field-only candidate. This is a display/
        # validation projection; the save branch still projects owned fields
        # onto the raw source after its awaited checks and revision guard.
        if action == "configuration_save":
            if message.get("revision") is not None:
                require_repair_revision(subentry, message["revision"])
            operation_revision = repair_revision(subentry)
        updates = message.get("config", {})
        if not isinstance(updates, dict):
            raise HomeAssistantError("config must be an object")
        if not state.field_updates_allowed:
            return {
                "valid": False,
                "errors": {
                    CONF_FUNCTION_TOOLS: "Repair the whole Function Tool collection before changing unrelated settings"
                },
            }
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

    if action == "configuration_validate":
        result = management_ui._validation_result(
            lambda: management_ui.agent_config_snapshot(
                management_ui.merge_agent_config(safe_base, filtered)
            )
        )
        if result["valid"]:
            try:
                await management_ui._async_validate_configuration_request(
                    hass, entry, subentry, result["config"]
                )
            except HomeAssistantError as err:
                return {"valid": False, "errors": {"api_mode": str(err)}}
            result["model_capabilities"] = management_ui.model_capabilities(
                result["config"][management_ui.CONF_CHAT_MODEL]
            )
        return result

    if action == "configuration_save":
        validation = management_ui._validation_result(
            lambda: management_ui.merge_agent_config(safe_base, filtered)
        )
        if not validation.get("valid"):
            return validation
        normalized = validation["config"]
        await management_ui._async_validate_configuration_request(
            hass, entry, subentry, normalized
        )
        require_repair_revision(subentry, operation_revision)
        persisted = project_agent_config_updates(subentry.data, filtered, normalized)

        requested_title = message.get("title")
        if requested_title is not None:
            if not isinstance(requested_title, str) or not requested_title.strip():
                return {"valid": False, "errors": {"title": "must not be empty"}}
            try:
                requested_title = management_ui.validate_agent_title(requested_title)
            except HomeAssistantError as err:
                return {"valid": False, "errors": {"title": str(err)}}
        saved_title = (
            requested_title.strip()
            if isinstance(requested_title, str)
            else subentry.title
        )
        update_configuration_subentry(
            hass,
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
                "revision": saved_agent_config_revision(
                    subentry, persisted, saved_title
                ),
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
        operation_revision = repair_revision(subentry)
        editable = editable_function_tools(dict(subentry.data))
        index = message.get("index")
        if not isinstance(editable, list) or not isinstance(index, int):
            raise HomeAssistantError("A valid Function Tool index is required")
        if index < 0 or index >= len(editable):
            raise HomeAssistantError(
                "The Function Tool changed position; reload and try again"
            )
        if not state.field_updates_allowed:
            raise HomeAssistantError(
                "Saved Function Tools cannot be isolated safely; repair the whole collection first"
            )
        current = editable[index]
        old_name = None
        if isinstance(current, dict) and isinstance(current.get("spec"), dict):
            candidate_name = current["spec"].get("name")
            if isinstance(candidate_name, str):
                old_name = candidate_name
        groups = subentry.data.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)

        if action == "delete_one":
            if old_name:
                _rules, references = await management_ui._function_reference_state(
                    hass, entry_id, subentry_id, subentry.data, old_name
                )
                if references["request_rules"] or references["guest_mode"]:
                    raise HomeAssistantError(
                        management_ui._function_reference_error(old_name, references)
                    )
            editable.pop(index)
            require_repair_revision(subentry, operation_revision)
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
            original_data = deepcopy(dict(subentry.data))
            rules = None
            rule_references = []
            extra_updates = {}
            if old_name and old_name != new_name:
                rules, _references = await management_ui._function_reference_state(
                    hass, entry_id, subentry_id, subentry.data, old_name
                )
                rule_references = _references.get("request_rules", [])
                rules_revision = rules.revision()
                extra_updates[management_ui.CONF_GUEST_ALLOWED_FUNCTION_NAMES] = [
                    new_name if name == old_name else name
                    for name in subentry.data.get(
                        management_ui.CONF_GUEST_ALLOWED_FUNCTION_NAMES, []
                    )
                ]
            require_repair_revision(subentry, operation_revision)
            await management_ui._async_validate_configuration_dependencies(
                hass,
                entry,
                subentry,
                {**subentry.data, CONF_FUNCTION_TOOLS: editable},
                renamed_function=(old_name, new_name)
                if old_name and old_name != new_name
                else None,
                rules_manager=rules,
            )
            require_repair_revision(subentry, operation_revision)
            persisted = _persist_raw_tools(
                hass,
                entry,
                subentry,
                editable,
                _replace_group_function_name(groups, old_name, new_name),
                extra_updates,
            )
            persisted_revision = saved_agent_config_revision(
                subentry, persisted, subentry.title
            )
            if rules is not None:
                assert old_name is not None
                try:
                    await rules.async_rename_function_reference(
                        old_name, new_name, expected_revision=rules_revision
                    )
                except (Exception, asyncio.CancelledError) as err:
                    from .function_dependency_integrity import (
                        renamed_function_references_are_saved,
                    )

                    rule_rename_saved = renamed_function_references_are_saved(
                        rules, old_name, new_name, rule_references
                    )
                    if rule_rename_saved is None:
                        if isinstance(err, asyncio.CancelledError):
                            raise
                        raise HomeAssistantError(
                            "Request Rule storage cannot be verified. The saved Function "
                            "Tool rename was retained; restore storage access and reload "
                            "to review Rule references."
                        ) from err
                    if rule_rename_saved:
                        if isinstance(err, asyncio.CancelledError):
                            raise
                        raise HomeAssistantError(
                            "Function Tool and Request Rule rename were saved, but the "
                            "save acknowledgement failed. Reload the latest saved settings."
                        ) from err
                    try:
                        require_repair_revision(subentry, persisted_revision)
                    except HomeAssistantError as rollback_err:
                        if isinstance(err, asyncio.CancelledError):
                            raise err from rollback_err
                        raise HomeAssistantError(
                            "Function Tool configuration changed while a related Request Rule "
                            "rename failed. The newer configuration was preserved; reload "
                            "before retrying."
                        ) from rollback_err
                    update_live_subentry(hass, entry, subentry, data=original_data)
                    raise

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
    operation_revision = repair_revision(subentry)
    candidate = message.get("tools")
    if not isinstance(candidate, (str, list)):
        raise HomeAssistantError("tools must be a list or YAML/JSON text")
    validated_tools = validate_function_tools(candidate)
    # Bulk Tool repair owns Tools only. Validate surviving usable Groups against
    # the proposed Tools while retaining unrelated raw Group damage for repair.
    validate_function_groups(state.groups, validated_tools)

    persisted = dict(subentry.data)
    persisted[CONF_FUNCTION_TOOLS] = dump_yaml(validated_tools, sort_keys=False)
    await management_ui._async_validate_configuration_request(
        hass, entry, subentry, safe_function_configuration(persisted)
    )
    await management_ui._async_validate_configuration_dependencies(
        hass, entry, subentry, persisted
    )
    require_repair_revision(subentry, operation_revision)
    update_live_subentry(hass, entry, subentry, data=persisted)
    payload = _safe_configuration_payload(
        hass, management_ui, management_loading_performance, entry, subentry
    )
    payload.update(
        {
            "valid": True,
            "tools": deepcopy(validated_tools),
            "agent": management_loading_performance._agent_snapshot(
                hass, entry, subentry, config=persisted
            ),
        }
    )
    return payload
