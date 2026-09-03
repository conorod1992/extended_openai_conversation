"""Focused authorization and resource guards for model-facing runtime paths."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.util.dt as dt_util

from .ha_permissions import get_active_ha_context, set_active_ha_context
from .resource_limits import MAX_NATIVE_SERVICE_ACTIONS

MAX_HISTORY_ENTITY_IDS = 50
MAX_HISTORY_SPAN = timedelta(days=31)
MAX_STATISTIC_SPAN_BY_PERIOD: dict[str, timedelta] = {
    "5minute": timedelta(days=7),
    "hour": timedelta(days=90),
    "day": timedelta(days=366 * 5),
    "month": timedelta(days=366 * 20),
}

_INSTALLED = False


def install_safety_hardening() -> None:
    """Install focused guards missed by the earlier release hardening passes."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_delayed_permission_context()
    _install_native_tool_guards()
    _install_broadcast_state_transactions()
    _update_builtin_resource_schema()
    _INSTALLED = True


def _install_delayed_permission_context() -> None:
    """Restore the persisted HA caller identity around recovered delayed tools."""
    from .delayed_tools import DelayedToolManager

    current = DelayedToolManager._async_execute_due
    if getattr(current, "_extended_openai_permission_context", False):
        return
    original = current

    async def async_execute_due(manager: Any, call_id: str) -> bool:
        record = manager._records.get(call_id)
        if record is None:
            return await original(manager, call_id)

        # ContextVars are copied when asyncio tasks are created. Explicitly replace
        # any inherited request context with the durable origin (including anonymous)
        # so restart recovery and same-process execution behave identically.
        previous = get_active_ha_context()
        set_active_ha_context(Context(user_id=record.user_id))
        try:
            return await original(manager, call_id)
        finally:
            set_active_ha_context(previous)

    async_execute_due._extended_openai_permission_context = True  # type: ignore[attr-defined]
    DelayedToolManager._async_execute_due = async_execute_due  # type: ignore[method-assign,assignment]


async def _async_require_admin(hass: HomeAssistant, llm_context: Any) -> None:
    """Require an active authenticated HA administrator for durable HA mutation."""
    context = getattr(llm_context, "context", None) or get_active_ha_context()
    user_id = getattr(context, "user_id", None)
    if not isinstance(user_id, str) or not user_id:
        raise HomeAssistantError(
            "add_automation requires an authenticated Home Assistant administrator"
        )
    user = await hass.auth.async_get_user(user_id)
    if (
        user is None
        or getattr(user, "is_active", True) is not True
        or getattr(user, "is_admin", False) is not True
    ):
        raise HomeAssistantError(
            "add_automation requires an active Home Assistant administrator"
        )


def _parse_datetime(value: Any, label: str) -> Any:
    """Parse one required ISO datetime to UTC."""
    if not isinstance(value, str):
        raise HomeAssistantError(f"{label} must be an ISO 8601 datetime")
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise HomeAssistantError(f"{label} must be an ISO 8601 datetime")
    return dt_util.as_utc(parsed)


def _validate_time_window(start: Any, end: Any, maximum: timedelta) -> None:
    """Reject inverted and excessively expensive Recorder windows."""
    if end <= start:
        raise HomeAssistantError("end_time must be after start_time")
    if end - start > maximum:
        raise HomeAssistantError(
            f"Requested Recorder time range may not exceed {maximum.days} days"
        )


def _validate_history_request(arguments: dict[str, Any]) -> None:
    """Bound history cardinality and time before Recorder materializes results."""
    entity_ids = arguments.get("entity_ids")
    if (
        not isinstance(entity_ids, list)
        or not entity_ids
        or any(
            not isinstance(entity_id, str) or not entity_id for entity_id in entity_ids
        )
    ):
        raise HomeAssistantError("entity_ids must be a non-empty list of entity IDs")
    if len(entity_ids) > MAX_HISTORY_ENTITY_IDS:
        raise HomeAssistantError(
            f"entity_ids may contain at most {MAX_HISTORY_ENTITY_IDS} IDs"
        )

    now = dt_util.utcnow()
    raw_start = arguments.get("start_time")
    start = (
        _parse_datetime(raw_start, "start_time")
        if raw_start is not None
        else now - timedelta(days=1)
    )
    raw_end = arguments.get("end_time")
    end = (
        _parse_datetime(raw_end, "end_time")
        if raw_end is not None
        else start + timedelta(days=1)
    )
    _validate_time_window(start, end, MAX_HISTORY_SPAN)


def _validate_execute_service_request(arguments: dict[str, Any]) -> None:
    """Bound the number of Home Assistant actions inside one native tool call."""
    actions = arguments.get("list")
    if not isinstance(actions, list):
        raise HomeAssistantError("execute_service list must be an array")
    if len(actions) > MAX_NATIVE_SERVICE_ACTIONS:
        raise HomeAssistantError(
            f"execute_service may contain at most {MAX_NATIVE_SERVICE_ACTIONS} actions"
        )


def _normalized_statistics_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate the period/window and normalize the optional period default."""
    normalized = dict(arguments)
    period = normalized.get("period") or "day"
    if period not in MAX_STATISTIC_SPAN_BY_PERIOD:
        raise HomeAssistantError("Unsupported statistics period")
    normalized["period"] = period
    start = _parse_datetime(normalized.get("start_time"), "start_time")
    end = _parse_datetime(normalized.get("end_time"), "end_time")
    _validate_time_window(start, end, MAX_STATISTIC_SPAN_BY_PERIOD[period])
    return normalized


def _install_native_tool_guards() -> None:
    """Add admin, Recorder, and action work bounds to native tools."""
    from .functions import native as native_module
    from .functions.native import NativeFunction

    current_native_call = native_module.async_call_ha_action
    if not getattr(current_native_call, "_extended_openai_blocking_native", False):
        original_native_call = current_native_call

        async def async_call_ha_action_blocking(*args: Any, **kwargs: Any) -> Any:
            kwargs["blocking"] = True
            return await original_native_call(*args, **kwargs)

        async_call_ha_action_blocking._extended_openai_blocking_native = True  # type: ignore[attr-defined]
        native_module.async_call_ha_action = async_call_ha_action_blocking  # type: ignore[assignment]

    current_execute = NativeFunction.execute_service
    if not getattr(current_execute, "_extended_openai_batch_guard", False):
        original_execute = current_execute

        async def execute_service(
            function: Any,
            hass: HomeAssistant,
            function_config: dict[str, Any],
            arguments: dict[str, Any],
            llm_context: Any,
            exposed_entities: list[dict[str, Any]],
        ) -> Any:
            _validate_execute_service_request(arguments)
            return await original_execute(
                function,
                hass,
                function_config,
                arguments,
                llm_context,
                exposed_entities,
            )

        execute_service._extended_openai_batch_guard = True  # type: ignore[attr-defined]
        NativeFunction.execute_service = execute_service  # type: ignore[method-assign,assignment]

    current_add = NativeFunction.add_automation
    if not getattr(current_add, "_extended_openai_admin_guard", False):
        original_add = current_add

        async def add_automation(
            function: Any,
            hass: HomeAssistant,
            function_config: dict[str, Any],
            arguments: dict[str, Any],
            llm_context: Any,
            exposed_entities: list[dict[str, Any]],
        ) -> str:
            await _async_require_admin(hass, llm_context)
            return await original_add(
                function,
                hass,
                function_config,
                arguments,
                llm_context,
                exposed_entities,
            )

        add_automation._extended_openai_admin_guard = True  # type: ignore[attr-defined]
        NativeFunction.add_automation = add_automation  # type: ignore[method-assign,assignment]

    current_history = NativeFunction.get_history
    if not getattr(current_history, "_extended_openai_recorder_guard", False):
        original_history = current_history

        async def get_history(
            function: Any,
            hass: HomeAssistant,
            function_config: dict[str, Any],
            arguments: dict[str, Any],
            llm_context: Any,
            exposed_entities: list[dict[str, Any]],
        ) -> Any:
            _validate_history_request(arguments)
            return await original_history(
                function,
                hass,
                function_config,
                arguments,
                llm_context,
                exposed_entities,
            )

        get_history._extended_openai_recorder_guard = True  # type: ignore[attr-defined]
        NativeFunction.get_history = get_history  # type: ignore[method-assign,assignment]

    current_statistics = NativeFunction.get_statistics
    if not getattr(current_statistics, "_extended_openai_recorder_guard", False):
        original_statistics = current_statistics

        async def get_statistics(
            function: Any,
            hass: HomeAssistant,
            function_config: dict[str, Any],
            arguments: dict[str, Any],
            llm_context: Any,
            exposed_entities: list[dict[str, Any]],
        ) -> Any:
            normalized = _normalized_statistics_arguments(arguments)
            return await original_statistics(
                function,
                hass,
                function_config,
                normalized,
                llm_context,
                exposed_entities,
            )

        get_statistics._extended_openai_recorder_guard = True  # type: ignore[attr-defined]
        NativeFunction.get_statistics = get_statistics  # type: ignore[method-assign,assignment]


def _broadcast_state_lock(manager: Any) -> asyncio.Lock:
    lock = getattr(manager, "_extended_openai_state_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        manager.__dict__["_extended_openai_state_lock"] = lock
    return lock


def _expire_pending_broadcasts(manager: Any) -> None:
    """Expire only queued deliveries after the disabled state is durable."""
    for entity_id, queue in list(manager._queues.items()):
        retained: deque[Any] = deque()
        for item in queue:
            delivery = item.deliveries.get(entity_id)
            if delivery is None:
                continue
            if delivery.status == "delivering":
                retained.append(item)
                continue
            if delivery.status not in {"delivered", "failed", "expired"}:
                delivery.set("expired", "broadcast_disabled")
        queue.clear()
        queue.extend(retained)
        if not queue:
            manager._queues.pop(entity_id, None)


def _install_broadcast_state_transactions() -> None:
    """Serialize Broadcast settings and persist before publishing live changes."""
    from .intercom import IntercomManager

    current_initialize = IntercomManager.async_initialize
    if not getattr(current_initialize, "_extended_openai_state_guard", False):

        async def async_initialize(manager: Any) -> None:
            lock = _broadcast_state_lock(manager)
            async with lock:
                if manager._loaded:
                    return
                stored = await manager._store.async_load()
                enabled = bool(stored.get("enabled", False)) if stored else False
                manager._enabled = enabled
                manager._loaded = True

        async_initialize._extended_openai_state_guard = True  # type: ignore[attr-defined]
        IntercomManager.async_initialize = async_initialize  # type: ignore[method-assign,assignment]

    current_set = IntercomManager.async_set_enabled
    if not getattr(current_set, "_extended_openai_state_guard", False):

        async def async_set_enabled(manager: Any, enabled: bool) -> None:
            new_enabled = bool(enabled)
            lock = _broadcast_state_lock(manager)
            async with lock:
                await manager._store.async_save({"enabled": new_enabled})
                manager._enabled = new_enabled
                manager._loaded = True
                if not new_enabled:
                    _expire_pending_broadcasts(manager)

        async_set_enabled._extended_openai_state_guard = True  # type: ignore[attr-defined]
        IntercomManager.async_set_enabled = async_set_enabled  # type: ignore[method-assign,assignment]


def _update_builtin_resource_schema() -> None:
    """Tell the model about built-in native cardinality bounds before execution."""
    from .built_in_functions import BUILT_IN_FUNCTION_PRESETS

    for preset in BUILT_IN_FUNCTION_PRESETS:
        implementation = preset.get("implementation")
        properties = preset["tool"]["spec"]["parameters"]["properties"]
        if implementation == "get_history":
            entity_schema = properties["entity_ids"]
            entity_schema["minItems"] = 1
            entity_schema["maxItems"] = MAX_HISTORY_ENTITY_IDS
        elif implementation == "execute_service":
            properties["list"]["maxItems"] = MAX_NATIVE_SERVICE_ACTIONS
