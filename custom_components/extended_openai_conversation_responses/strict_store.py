"""Make critical HA Store write failures visible to EOAI transactions.

HA Store logs and swallows serialization and file-writer failures. EOAI's
transactions must receive those failures before publishing the mutation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

from .agent_maintenance import AgentMaintenanceGate, get_agent_maintenance_gate
from .operation_settlement import async_settle_owned_operation as _async_settle_store_io
from .operational_errors import storage_failure_reason


class RecoveryGuardedStore(Store[dict[str, Any]]):
    """Preserve Store failure semantics while excluding pending restore generations."""

    _recovery_gate: AgentMaintenanceGate | None = None

    def bind_agent(self, entry_id: str, subentry_id: str) -> Self:
        gate = get_agent_maintenance_gate(self.hass, entry_id, subentry_id)
        if self._recovery_gate is not None and self._recovery_gate is not gate:
            self._recovery_gate.unregister_store(self)
        self._recovery_gate = gate
        gate.register_store(self)
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
            return await _async_settle_store_io(super().async_load())

    async def async_save(self, data: dict[str, Any]) -> None:
        if self._recovery_gate is None:
            await super().async_save(data)
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_store_io(super().async_save(data))

    async def _async_handle_write_data(self) -> None:
        # Delayed Store callbacks enter here before HA takes its native writer
        # lock. Waiting for maintenance while holding that lock would deadlock
        # recovery's own save to this same Store.
        if self._recovery_gate is None:
            await super()._async_handle_write_data()
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_store_io(super()._async_handle_write_data())

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        if self._recovery_gate is None:
            await super()._async_write_data(data)
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_store_io(super()._async_write_data(data))

    async def async_remove(self) -> None:
        if self._recovery_gate is None:
            await super().async_remove()
            return
        async with self._recovery_gate.shared(maintenance=True):
            await _async_settle_store_io(super().async_remove())


class PropagatingWriteStore(RecoveryGuardedStore):
    """Preserve HA's writer, but propagate sanitized failures before acknowledgement."""

    async def _async_write_data(self, data: dict[str, Any]) -> None:
        try:
            await super()._async_write_data(data)
        except SerializationError:
            # HA's outer handler would log the offending private value and return
            # normally. Use a different exception type so transactions reconcile
            # instead of publishing an unpersisted candidate as committed.
            raise OSError(
                "Private storage serialization failed; "
                "the EOAI state change could not be persisted"
            ) from None
        except WriteError as error:
            cause = error.__cause__
            number = cause.errno if isinstance(cause, OSError) else None
            # Do not expose the HA storage path or a private payload upstream.
            raise OSError(
                number,
                f"Private storage write failed: {storage_failure_reason(number)}; "
                "the EOAI state change could not be persisted",
            ) from None


async def async_remove_agent_stores(gate: AgentMaintenanceGate) -> None:
    """Retire actual Store callbacks before erasure under exclusive ownership.

    Disk orphan discovery remains separate: weak ownership covers live callbacks,
    not files whose handles have already gone away. All manager workers must have
    settled before the caller enters this deletion phase.
    """
    if not gate.owns_exclusive():
        raise RuntimeError("Store deletion requires exclusive agent maintenance")
    for store in gate.storage_resources:
        store._async_cleanup_delay_listener()
        store._async_cleanup_final_write_listener()
        await store.async_remove()


@asynccontextmanager
async def async_storage_lock(storage: Any, lock: asyncio.Lock) -> AsyncIterator[None]:
    """Own manager settlement before taking its lock, including reconciliation."""
    store = (
        storage
        if isinstance(storage, RecoveryGuardedStore)
        else getattr(storage, "_store", None)
    )
    if isinstance(store, RecoveryGuardedStore) and store._recovery_gate is not None:
        async with store._recovery_gate.shared(maintenance=True), lock:
            yield
    else:
        async with lock:
            yield
