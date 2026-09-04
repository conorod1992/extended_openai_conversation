"""Per-agent quiescence barrier for multi-store maintenance operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import wraps
import inspect
from typing import Any, cast

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_AGENT_MAINTENANCE_GATES = f"{DOMAIN}.agent_maintenance_gates"
_INSTALLED = False


class AgentMaintenanceGate:
    """Writer-preferring reader/writer gate for one conversation agent."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_readers = 0
        self._reader_depth: dict[asyncio.Task[Any], int] = {}
        self._waiting_writers = 0
        self._writer_active = False

    @asynccontextmanager
    async def shared(self) -> AsyncIterator[None]:
        """Enter ordinary agent work, waiting behind pending maintenance."""
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always supplies one here
            raise RuntimeError("Agent maintenance reader requires an asyncio task")

        async with self._condition:
            depth = self._reader_depth.get(task, 0)
            if depth:
                self._reader_depth[task] = depth + 1
            else:
                await self._condition.wait_for(
                    lambda: not self._writer_active and self._waiting_writers == 0
                )
                self._reader_depth[task] = 1
                self._active_readers += 1
        try:
            yield
        finally:
            async with self._condition:
                depth = self._reader_depth[task]
                if depth > 1:
                    self._reader_depth[task] = depth - 1
                else:
                    del self._reader_depth[task]
                    self._active_readers -= 1
                    if self._active_readers == 0:
                        self._condition.notify_all()

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Drain readers and exclude new work for one maintenance operation."""
        acquired = False
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._condition.wait_for(
                    lambda: not self._writer_active and self._active_readers == 0
                )
                self._writer_active = True
                acquired = True
            finally:
                self._waiting_writers -= 1
                if not acquired:
                    # A cancelled writer must not strand readers behind its waiter bit.
                    self._condition.notify_all()
        try:
            yield
        finally:
            async with self._condition:
                self._writer_active = False
                self._condition.notify_all()


def get_agent_maintenance_gate(
    hass: HomeAssistant, entry_id: str, subentry_id: str
) -> AgentMaintenanceGate:
    """Return the process-local gate for one exact agent."""
    gates = cast(
        dict[tuple[str, str], AgentMaintenanceGate],
        hass.data.setdefault(_AGENT_MAINTENANCE_GATES, {}),
    )
    return gates.setdefault((entry_id, subentry_id), AgentMaintenanceGate())


async def _async_run_exclusive_operation[T](
    gate: AgentMaintenanceGate,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """Finish one exclusive operation before propagating caller cancellation."""
    cancellation: asyncio.CancelledError | None = None
    async with gate.exclusive():
        task: asyncio.Future[T] = asyncio.ensure_future(operation())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as err:
                if task.cancelled():
                    raise
                if cancellation is None:
                    cancellation = err
            except Exception:
                # The operation has reached an outcome. Inspect it below so its
                # deterministic restore/rollback error takes precedence over a
                # concurrent caller cancellation.
                break

        result = task.result()

    # Release exclusivity before surfacing deferred cancellation so the next
    # ordinary operation cannot observe a permanently wedged maintenance gate.
    if cancellation is not None:
        raise cancellation
    return result


class _SharedGateProxy:
    """Gate async methods on a manager returned to a direct HA service handler."""

    def __init__(self, target: Any, gate: AgentMaintenanceGate) -> None:
        self._target = target
        self._gate = gate

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if not inspect.iscoroutinefunction(value):
            return value

        @wraps(value)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            async with self._gate.shared():
                return await value(*args, **kwargs)

        return guarded


def _install_conversation_guard() -> None:
    from .conversation import ExtendedOpenAIAgentEntity

    current = ExtendedOpenAIAgentEntity._async_process
    if getattr(current, "_extended_openai_maintenance_gate", False):
        return

    @wraps(current)
    async def guarded(entity: Any, *args: Any, **kwargs: Any) -> Any:
        gate = get_agent_maintenance_gate(
            entity.hass, entity.entry.entry_id, entity.subentry.subentry_id
        )
        async with gate.shared():
            return await current(entity, *args, **kwargs)

    guarded._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
    ExtendedOpenAIAgentEntity._async_process = guarded


def _install_backup_guards() -> None:
    from . import backup, management_ui

    current_create = backup.async_create_backup
    if not getattr(current_create, "_extended_openai_maintenance_gate", False):

        @wraps(current_create)
        async def guarded_create(
            hass: HomeAssistant, entry: Any, subentry: Any
        ) -> dict[str, Any]:
            gate = get_agent_maintenance_gate(
                hass, entry.entry_id, subentry.subentry_id
            )
            async with gate.shared():
                return await current_create(hass, entry, subentry)

        guarded_create._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
        backup.async_create_backup = guarded_create
        # management_ui imported this function by name during module import.
        management_ui.async_create_backup = guarded_create

    current_restore = backup.async_restore_backup
    if not getattr(current_restore, "_extended_openai_maintenance_gate", False):

        @wraps(current_restore)
        async def guarded_restore(
            hass: HomeAssistant, entry: Any, subentry: Any, value: Any
        ) -> dict[str, Any]:
            gate = get_agent_maintenance_gate(
                hass, entry.entry_id, subentry.subentry_id
            )
            return await _async_run_exclusive_operation(
                gate, lambda: current_restore(hass, entry, subentry, value)
            )

        guarded_restore._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
        backup.async_restore_backup = guarded_restore
        management_ui.async_restore_backup = guarded_restore


def _management_operation_owns_its_gate(message: dict[str, Any]) -> bool:
    """Avoid nesting around paths that enter a guarded operation in another task."""
    section = message.get("section", "overview")
    action = message.get("action")
    return section == "backup" or (
        (section == "request_rules" and action == "test")
        or (section == "diagnostics" and action == "test_agent")
    )


def _install_management_guard() -> None:
    from . import management_ui

    current = management_ui.async_management_command
    if getattr(current, "_extended_openai_maintenance_gate", False):
        return

    @wraps(current)
    async def guarded(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        if message.get("action") == "agents" or _management_operation_owns_its_gate(
            message
        ):
            return await current(hass, user_id, is_admin, message)
        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            return await current(hass, user_id, is_admin, message)
        gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
        async with gate.shared():
            return await current(hass, user_id, is_admin, message)

    guarded._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
    management_ui.async_management_command = guarded


def _install_legacy_memory_guard() -> None:
    from . import memory_ui

    current = memory_ui.async_manage_command
    if getattr(current, "_extended_openai_maintenance_gate", False):
        return

    @wraps(current)
    async def guarded(
        hass: HomeAssistant, user_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        if message.get("action") in {"agents", "test_agent"}:
            return await current(hass, user_id, message)
        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            return await current(hass, user_id, message)
        gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
        async with gate.shared():
            return await current(hass, user_id, message)

    guarded._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
    memory_ui.async_manage_command = guarded


def _install_service_guards() -> None:
    from . import services

    current_memory_getter = services.async_get_memory
    if not getattr(current_memory_getter, "_extended_openai_maintenance_gate", False):

        @wraps(current_memory_getter)
        async def guarded_memory_getter(
            hass: HomeAssistant, entry_id: str, subentry_id: str
        ) -> Any:
            gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
            async with gate.shared():
                manager = await current_memory_getter(hass, entry_id, subentry_id)
            return _SharedGateProxy(manager, gate)

        guarded_memory_getter._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
        services.async_get_memory = guarded_memory_getter

    current_guest_getter = services.async_get_guest_mode
    if not getattr(current_guest_getter, "_extended_openai_maintenance_gate", False):

        @wraps(current_guest_getter)
        async def guarded_guest_getter(
            hass: HomeAssistant, entry_id: str, subentry_id: str
        ) -> Any:
            gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
            async with gate.shared():
                manager = await current_guest_getter(hass, entry_id, subentry_id)
            return _SharedGateProxy(manager, gate)

        guarded_guest_getter._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
        services.async_get_guest_mode = guarded_guest_getter

    current_tool_state = services.async_set_function_tools_enabled
    if not getattr(current_tool_state, "_extended_openai_maintenance_gate", False):

        @wraps(current_tool_state)
        async def guarded_tool_state(
            hass: HomeAssistant,
            entry_id: str,
            agent_reference: str,
            function_names: list[str],
            enabled: bool,
        ) -> None:
            _, subentry_id = services.resolve_memory_agent(
                hass, entry_id, agent_reference
            )
            gate = get_agent_maintenance_gate(hass, entry_id, subentry_id)
            async with gate.shared():
                await current_tool_state(
                    hass,
                    entry_id,
                    agent_reference,
                    function_names,
                    enabled,
                )

        guarded_tool_state._extended_openai_maintenance_gate = True  # type: ignore[attr-defined]
        services.async_set_function_tools_enabled = guarded_tool_state


def install_agent_maintenance_barrier() -> None:
    """Install the final per-agent maintenance boundary around public entry points."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_conversation_guard()
    _install_backup_guards()
    _install_management_guard()
    _install_legacy_memory_guard()
    _install_service_guards()
    _INSTALLED = True
