"""Make critical HA Store write failures visible to EOAI transactions.

HA Store logs and swallows WriteError from its file writer. EOAI's rollback
managers must instead receive that failure before publishing the mutation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, Self

from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError

from .agent_maintenance import AgentMaintenanceGate, get_agent_maintenance_gate
from .operational_errors import storage_failure_reason


async def _async_settle_write(operation: Awaitable[None]) -> None:
    """Keep storage ownership until surviving native work has finished."""
    task = asyncio.ensure_future(operation)
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as err:
            if task.cancelled():
                raise
            cancelled = cancelled or err
        except Exception:
            break
    task.result()
    if cancelled is not None:
        raise cancelled


class RecoveryGuardedStore(Store[dict[str, Any]]):
    """Preserve Store failure semantics while excluding pending restore generations."""

    _recovery_gate: AgentMaintenanceGate | None = None

    def bind_agent(self, entry_id: str, subentry_id: str) -> Self:
        self._recovery_gate = get_agent_maintenance_gate(
            self.hass, entry_id, subentry_id
        )
        return self

    @property
    def recovery_pending(self) -> bool:
        return self._recovery_gate is not None and self._recovery_gate.recovery_required

    def require_available(self) -> None:
        if self._recovery_gate is not None:
            self._recovery_gate.require_available()

    async def async_load(self) -> dict[str, Any] | None:
        if self._recovery_gate is None:
            return await super().async_load()
        async with self._recovery_gate.shared(maintenance=True):
            return await super().async_load()

    async def async_save(self, data: dict[str, Any]) -> None:
        if self._recovery_gate is None:
            await super().async_save(data)
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_write(super().async_save(data))

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        if self._recovery_gate is None:
            await super()._async_write_data(data)
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_write(super()._async_write_data(data))

    async def async_remove(self) -> None:
        if self._recovery_gate is None:
            await super().async_remove()
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_write(super().async_remove())


class PropagatingWriteStore(RecoveryGuardedStore):
    """Preserve HA's atomic writer, but surface its OS failure to the caller."""

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        try:
            await super()._async_write_data(data)
        except WriteError as error:
            cause = error.__cause__
            number = cause.errno if isinstance(cause, OSError) else None
            # Do not expose the HA storage path or a private payload upstream.
            raise OSError(
                number,
                f"Private storage write failed: {storage_failure_reason(number)}; "
                "the EOAI state change could not be persisted",
            ) from None
