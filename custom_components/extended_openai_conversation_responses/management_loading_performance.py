"""Management navigation catalogs, bounded Overview data and config snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from copy import deepcopy
from time import perf_counter
from typing import Any, TypeVar

import yaml

from homeassistant.core import HomeAssistant

from .agent_config import (
    AGENT_CONFIG_FIELDS,
    agent_config_defaults,
    validate_function_groups,
    validate_function_tools,
)
from .const import (
    CONF_API_PROVIDER,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_TEMPORARY_MEMORY,
    DEFAULT_API_PROVIDER,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_CHAT_MODEL,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_TEMPORARY_MEMORY,
    DOMAIN,
)
from .conversation_archive import async_get_archive
from .feature_status import management_feature_status
from .guest_mode import async_get_guest_mode, get_loaded_guest_mode
from .knowledge import async_get_knowledge, get_loaded_knowledge
from .management_function_repair import (
    management_function_tool_health,
    peek_function_tool_health,
)
from .management_history_queries import usage_summary
from .management_projections import async_scope_catalog_projection, settings_snapshot
from .management_setup_health import add_setup_health, build_setup_health_facts
from .memory import async_get_memory, get_memory_mode
from .temporary_memory import async_get_temporary_memory
from .usage import async_get_usage

_T = TypeVar("_T")


def _ms(start: float) -> float:
    return round((perf_counter() - start) * 1000, 2)


def _guest_has_ha_exclusions(options: dict[str, Any]) -> bool:
    return any(
        options.get(key)
        for key in (
            "guest_excluded_labels",
            "guest_excluded_areas",
            "guest_excluded_domains",
            "guest_excluded_entities",
        )
    )


def _agent_snapshot(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    config: dict[str, Any] | None = None,
    title: str | None = None,
    memory_count: int = 0,
    knowledge_source_count: int = 0,
    tokens_today: int = 0,
    guest_status: dict[str, Any] | None = None,
    function_tools_health: dict[str, Any] | None = None,
    function_tools_ms: float | None = None,
    performance: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the cheap frontend metadata shared by bootstrap and overview."""
    started = perf_counter()
    options = config if config is not None else dict(subentry.data)
    if function_tools_health is None:
        function_tools_health = peek_function_tool_health(options)
    if function_tools_health is None and config is not None:
        tools = config.get(CONF_FUNCTION_TOOLS)
        if isinstance(tools, list):
            function_tools_health = {
                "enabled_count": sum(
                    tool.get("enabled", True) is True
                    for tool in tools
                    if isinstance(tool, dict)
                ),
            }
    measured_function_tools_ms = function_tools_ms or 0.0
    if performance is not None:
        performance["function_tools_ms"] = measured_function_tools_ms
    function_issue = (function_tools_health or {}).get("validation_error")
    phase = perf_counter()
    if guest_status is None:
        loaded_guest = get_loaded_guest_mode(hass, entry.entry_id, subentry.subentry_id)
        guest_status = (
            dict(loaded_guest.status())
            if loaded_guest is not None
            else {"state": "unloaded", "currently_active": False}
        )
    else:
        guest_status = dict(guest_status)
    guest_status["has_home_assistant_exclusions"] = _guest_has_ha_exclusions(options)
    if performance is not None:
        performance["guest_status_ms"] = _ms(phase)
    snapshot = {
        "entry_id": entry.entry_id,
        "entry_title": entry.title,
        "subentry_id": subentry.subentry_id,
        "title": title if title is not None else subentry.title,
        "provider": entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER),
        "model": options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
        "memory_mode": get_memory_mode(options),
        "temporary_memory": options.get(
            CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY
        ),
        "memory_count": memory_count,
        "knowledge_enabled": bool(options.get(CONF_KNOWLEDGE_ENABLED, False)),
        "knowledge_source_count": knowledge_source_count,
        "function_count": (
            int(function_tools_health["enabled_count"])
            if function_tools_health is not None
            else None
        ),
        "function_group_count": len(
            options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS)
        ),
        "archive_enabled": bool(
            options.get(CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED)
        ),
        "tokens_today": tokens_today,
        "guest_mode": guest_status,
    }
    if knowledge_source_count == 0:
        loaded = get_loaded_knowledge(hass, entry.entry_id, subentry.subentry_id)
        if loaded is not None:
            knowledge_source_count = int(loaded.source_count)
            snapshot["knowledge_source_count"] = knowledge_source_count
    phase = perf_counter()
    snapshot["feature_status"] = management_feature_status(
        options, knowledge_source_count=knowledge_source_count
    )
    if performance is not None:
        performance["feature_status_ms"] = _ms(phase)
    if function_issue is not None:
        snapshot["configuration_issue"] = {
            "field": CONF_FUNCTION_TOOLS,
            "message": function_issue,
            "repairable": True,
        }
    if performance is not None:
        performance["total_ms"] = _ms(started)
    return snapshot


async def async_agent_catalog(
    hass: HomeAssistant, user_id: str, is_admin: bool
) -> dict[str, Any]:
    """Return startup navigation metadata without loading memory/archive scopes."""
    del user_id
    started = perf_counter()
    agents: list[dict[str, Any]] = []
    snapshot_timings: list[dict[str, float]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        for subentry in entry.subentries.values():
            if subentry.subentry_type != "conversation":
                continue
            timing: dict[str, float] = {}
            agents.append(
                _agent_snapshot(
                    hass,
                    entry,
                    subentry,
                    function_tools_health=peek_function_tool_health(
                        dict(subentry.data)
                    ),
                    performance=timing,
                )
            )
            snapshot_timings.append(timing)
    timings: dict[str, Any] = {
        "total_ms": _ms(started),
        "agent_count": len(agents),
        "snapshots": snapshot_timings,
    }
    return {"agents": agents, "is_admin": is_admin, "_performance": timings}


async def async_scope_catalog(
    hass: HomeAssistant,
    user_id: str,
    is_admin: bool,
    entry_id: str,
    subentry_id: str,
    *,
    scope_kind: str = "all",
) -> dict[str, Any]:
    """Load only the count family needed by the current Management route."""
    if scope_kind not in {"all", "archive", "memory", "temporary"}:
        raise ValueError("scope_kind must be all, archive, memory, or temporary")

    memory_counts: dict[str, int] = {}
    conversation_counts: dict[str, int] = {}
    temporary_counts: dict[str, int] = {}

    if scope_kind == "all":
        memory, archive, temporary_memory = await asyncio.gather(
            async_get_memory(hass, entry_id, subentry_id),
            async_get_archive(hass, entry_id, subentry_id),
            async_get_temporary_memory(hass, entry_id, subentry_id),
        )
        memory_counts = memory.scope_counts()
        conversation_counts = archive.scope_counts()
        temporary_counts = temporary_memory.owner_counts()
    elif scope_kind == "archive":
        archive = await async_get_archive(hass, entry_id, subentry_id)
        conversation_counts = archive.scope_counts()
    elif scope_kind == "memory":
        memory = await async_get_memory(hass, entry_id, subentry_id)
        memory_counts = memory.scope_counts()
    else:
        temporary_memory = await async_get_temporary_memory(hass, entry_id, subentry_id)
        temporary_counts = temporary_memory.owner_counts()

    return {
        "scopes": await async_scope_catalog_projection(
            hass,
            user_id,
            is_admin,
            memory_counts,
            conversation_counts,
            temporary_counts,
        )
    }


async def async_overview_primary(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    """Return the cheap authoritative Overview shell without Store initialization."""
    started = perf_counter()
    options = dict(subentry.data)
    function_tools_started = perf_counter()
    function_tools_health = peek_function_tool_health(options)
    function_tools_ms = _ms(function_tools_started)

    agent_timing: dict[str, float] = {}
    projection_started = perf_counter()
    agent = _agent_snapshot(
        hass,
        entry,
        subentry,
        function_tools_health=function_tools_health,
        function_tools_ms=function_tools_ms,
        performance=agent_timing,
    )
    result = {
        "agent": agent,
        "usage": {},
        "conversations": settings_snapshot(options),
        "load_errors": [],
        "loading": {
            "usage": True,
            "memory": True,
            "knowledge": True,
            "guest_mode": True,
            "setup_health": True,
        },
    }
    projection_ms = _ms(projection_started)

    health_started = perf_counter()
    try:
        facts = build_setup_health_facts(
            hass,
            entry,
            subentry,
            memory_available=False,
            knowledge_source_count=int(agent.get("knowledge_source_count", 0)),
            knowledge_available=False,
            is_admin=is_admin,
            function_tools_health=function_tools_health,
            include_exposed_entities=False,
            include_function_tools=False,
        )
        facts["memory"] = {**dict(facts.get("memory", {})), "loading": True}
        facts["knowledge"] = {**dict(facts.get("knowledge", {})), "loading": True}
        if function_tools_health is None:
            facts["function_tools"] = {"loading": True}
        facts["exposed_entity_count_loading"] = True
        result["setup_health"] = facts
    except Exception:
        result["setup_health"] = {
            "unavailable": True,
            "function_tools": (
                function_tools_health
                if function_tools_health is not None
                else {"loading": True}
            ),
            "exposed_entity_count_loading": True,
            "provider_runtime": {
                "client_loaded": getattr(entry, "runtime_data", None) is not None,
                "provider": str(
                    entry.data.get(CONF_API_PROVIDER, DEFAULT_API_PROVIDER)
                ),
                "model": str(options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)).strip(),
            },
            "can_manage": is_admin,
            "live_provider_tested": False,
        }

    result["_performance"] = {
        "projection_ms": projection_ms,
        "agent_snapshot": agent_timing,
        "setup_health_ms": _ms(health_started),
        "total_ms": _ms(started),
    }
    return result


async def async_overview_detail(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    is_admin: bool,
    kind: str,
) -> dict[str, Any]:
    """Load one independent storage-backed Overview detail."""
    entry_id = str(entry.entry_id)
    subentry_id = str(subentry.subentry_id)
    started = perf_counter()

    if kind == "usage":
        usage = usage_summary(await async_get_usage(hass, entry_id, subentry_id))
        if not is_admin:
            usage["latest"] = None
        tokens_today = int(usage["today"].get("total_tokens", 0))
        return {
            "kind": kind,
            "usage": usage,
            "agent": {"tokens_today": tokens_today},
            "_performance": {"total_ms": _ms(started)},
        }

    if kind == "memory":
        memory = await async_get_memory(hass, entry_id, subentry_id)
        return {
            "kind": kind,
            "agent": {"memory_count": int(memory.memory_count)},
            "setup_health": {"memory": {"available": True, "loading": False}},
            "_performance": {"total_ms": _ms(started)},
        }

    if kind == "knowledge":
        knowledge = await async_get_knowledge(hass, entry_id, subentry_id)
        source_count = int(knowledge.source_count)
        return {
            "kind": kind,
            "agent": {
                "knowledge_source_count": source_count,
                "feature_status": management_feature_status(
                    dict(subentry.data), knowledge_source_count=source_count
                ),
            },
            "setup_health": {
                "knowledge": {
                    "source_count": source_count,
                    "available": True,
                    "loading": False,
                }
            },
            "_performance": {"total_ms": _ms(started)},
        }

    if kind == "guest_mode":
        guest = await async_get_guest_mode(hass, entry_id, subentry_id)
        return {
            "kind": kind,
            "agent": {"guest_mode": guest.status()},
            "_performance": {"total_ms": _ms(started)},
        }

    if kind == "setup_health":
        phase = perf_counter()
        options = dict(subentry.data)
        health = management_function_tool_health(options)
        function_tools_ms = _ms(phase)
        try:
            from .management_setup_health import _exposed_entity_count

            exposure = _exposed_entity_count(hass)
        except Exception:
            exposure = None
        return {
            "kind": kind,
            "agent": {
                "function_count": int(health.get("enabled_count", 0)),
                **(
                    {
                        "configuration_issue": {
                            "field": CONF_FUNCTION_TOOLS,
                            "message": health["validation_error"],
                            "repairable": True,
                        }
                    }
                    if health.get("validation_error")
                    else {}
                ),
            },
            "setup_health": {
                "function_tools": health,
                "exposed_entity_count": exposure,
                "exposed_entity_count_loading": False,
            },
            "_performance": {
                "function_tools_ms": function_tools_ms,
                "total_ms": _ms(started),
            },
        }

    raise ValueError(
        "kind must be usage, memory, knowledge, guest_mode, or setup_health"
    )


async def async_overview_summary(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    *,
    is_admin: bool,
) -> dict[str, Any]:
    """Load and bound the already-selected agent's Overview in one request."""
    entry_id = str(entry.entry_id)
    subentry_id = str(subentry.subentry_id)

    started = perf_counter()
    loader_timings: dict[str, float] = {}

    async def timed(name: str, awaitable: Awaitable[_T]) -> _T:
        phase = perf_counter()
        try:
            return await awaitable
        finally:
            loader_timings[name] = _ms(phase)

    usage_result, memory_result, knowledge_result, guest_result = await asyncio.gather(
        timed("usage_load_ms", async_get_usage(hass, entry_id, subentry_id)),
        timed("memory_load_ms", async_get_memory(hass, entry_id, subentry_id)),
        timed("knowledge_load_ms", async_get_knowledge(hass, entry_id, subentry_id)),
        timed("guest_load_ms", async_get_guest_mode(hass, entry_id, subentry_id)),
        return_exceptions=True,
    )

    load_errors: list[dict[str, str]] = []

    def record_failure(key: str, label: str, error: BaseException) -> None:
        load_errors.append(
            {
                "key": key,
                "label": label,
                "message": str(error) or type(error).__name__,
            }
        )

    usage: dict[str, Any] = {}
    tokens_today = 0
    if isinstance(usage_result, BaseException):
        record_failure("usage", "Usage", usage_result)
    else:
        usage = usage_summary(usage_result)
        if not is_admin:
            usage["latest"] = None
        tokens_today = int(usage["today"].get("total_tokens", 0))

    memory_count = 0
    if isinstance(memory_result, BaseException):
        record_failure("memories", "Memory", memory_result)
    else:
        memory_count = int(memory_result.memory_count)

    knowledge_source_count = 0
    if isinstance(knowledge_result, BaseException):
        record_failure("knowledge", "Knowledge", knowledge_result)
    else:
        knowledge_source_count = int(knowledge_result.source_count)

    guest_status: dict[str, Any] | None = None
    if isinstance(guest_result, BaseException):
        record_failure("guest_mode", "Guest Mode", guest_result)
    else:
        guest_status = guest_result.status()

    function_tools_started = perf_counter()
    function_tools_health = management_function_tool_health(dict(subentry.data))
    function_tools_ms = _ms(function_tools_started)

    projection_started = perf_counter()
    agent_timing: dict[str, float] = {}
    result = {
        "agent": _agent_snapshot(
            hass,
            entry,
            subentry,
            memory_count=memory_count,
            knowledge_source_count=knowledge_source_count,
            tokens_today=tokens_today,
            guest_status=guest_status,
            function_tools_health=function_tools_health,
            function_tools_ms=function_tools_ms,
            performance=agent_timing,
        ),
        "usage": usage,
        "conversations": settings_snapshot(dict(subentry.data)),
        "load_errors": load_errors,
    }
    projection_ms = _ms(projection_started)
    health_started = perf_counter()
    result = add_setup_health(
        hass,
        entry,
        subentry,
        result,
        is_admin=is_admin,
        function_tools_health=function_tools_health,
    )
    timings: dict[str, Any] = {
        **loader_timings,
        "projection_ms": projection_ms,
        "agent_snapshot": agent_timing,
        "setup_health_ms": _ms(health_started),
        "total_ms": _ms(started),
    }
    result["_performance"] = timings
    return result


def _snapshot_normalized_configuration(
    config: dict[str, Any], *, validated: bool = False
) -> dict[str, Any]:
    """Build a frontend snapshot, trusting tools only after a successful save."""
    snapshot = agent_config_defaults()
    snapshot.update(
        {
            key: deepcopy(value)
            for key, value in config.items()
            if key in AGENT_CONFIG_FIELDS
        }
    )
    if validated:
        function_tools = snapshot[CONF_FUNCTION_TOOLS]
        if isinstance(function_tools, str):
            function_tools = yaml.safe_load(function_tools) or []
    else:
        function_tools = validate_function_tools(snapshot[CONF_FUNCTION_TOOLS])
    snapshot[CONF_FUNCTION_TOOLS] = function_tools
    if not validated:
        snapshot[CONF_FUNCTION_GROUPS] = validate_function_groups(
            snapshot[CONF_FUNCTION_GROUPS], function_tools
        )
    return snapshot
