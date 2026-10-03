"""Strict HA Store failure propagation and sanitization."""

from __future__ import annotations

import asyncio
import errno

import pytest

from custom_components.extended_openai_conversation_responses.strict_store import (
    PropagatingWriteStore,
)
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError


@pytest.mark.asyncio
async def test_strict_store_surfaces_errno_without_private_path_or_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_write(_self, _data):
        try:
            raise OSError(errno.ENOSPC, "disk full at /config/.storage/private")
        except OSError as cause:
            raise WriteError("writer leaked /config/.storage/private") from cause

    monkeypatch.setattr(Store, "_async_write_data", fail_write)
    store = object.__new__(PropagatingWriteStore)
    private = {"secret": "PRIVATE-CONTENT-MARKER"}

    with pytest.raises(OSError) as raised:
        await store._async_write_data(private)

    assert raised.value.errno == errno.ENOSPC
    rendered = str(raised.value)
    assert "Private storage write failed" in rendered
    assert "/config/.storage/private" not in rendered
    assert "PRIVATE-CONTENT-MARKER" not in rendered
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_strict_store_handles_writer_error_without_oserror_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_write(_self, _data):
        raise WriteError("writer failed")

    monkeypatch.setattr(Store, "_async_write_data", fail_write)
    store = object.__new__(PropagatingWriteStore)

    with pytest.raises(OSError) as raised:
        await store._async_write_data({})

    assert raised.value.errno is None
    assert "Private storage write failed" in str(raised.value)


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("operation", ["async_load", "_async_write_data"])
async def test_cancelled_scoped_io_keeps_restore_excluded_until_settlement(
    monkeypatch, failure, operation
):
    """Repeated cancellation cannot release the reader around surviving I/O."""
    from custom_components.extended_openai_conversation_responses.agent_maintenance import (
        AgentMaintenanceGate,
    )
    from custom_components.extended_openai_conversation_responses.strict_store import (
        RecoveryGuardedStore,
    )

    gate = AgentMaintenanceGate()
    entered, release, writer_queued, restore_entered = (
        asyncio.Event() for _ in range(4)
    )
    store = object.__new__(RecoveryGuardedStore)
    store._recovery_gate = gate

    async def held_native(_self, *_args):
        entered.set()
        await release.wait()
        if failure:
            raise OSError("settled native I/O failed")

    wait_for = gate._condition.wait_for

    async def observed_wait(predicate):
        if gate._waiting_writers:
            writer_queued.set()
        return await wait_for(predicate)

    monkeypatch.setattr(Store, operation, held_native)
    monkeypatch.setattr(gate._condition, "wait_for", observed_wait)
    io = (
        store.async_load()
        if operation == "async_load"
        else store._async_write_data({"data": {}})
    )
    task = asyncio.create_task(io)
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and gate._active_readers == 1

    async def restore():
        async with gate.exclusive():
            restore_entered.set()

    maintenance = asyncio.create_task(restore())
    await writer_queued.wait()
    assert not restore_entered.is_set() and not maintenance.done()
    release.set()
    with pytest.raises(OSError if failure else asyncio.CancelledError):
        await task
    await maintenance
    assert restore_entered.is_set() and gate._active_readers == 0
