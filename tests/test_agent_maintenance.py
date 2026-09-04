"""Concurrency regressions for per-agent backup restore quiescence."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    AgentMaintenanceGate,
    _SharedGateProxy,
    _async_run_exclusive_operation,
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.backup import BackupError


async def test_writer_waits_for_reader_and_blocks_late_reader() -> None:
    """A pending restore drains current work without allowing reader starvation."""
    gate = AgentMaintenanceGate()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    writer_entered = asyncio.Event()
    release_writer = asyncio.Event()
    late_entered = asyncio.Event()

    async def first_reader() -> None:
        async with gate.shared():
            first_entered.set()
            await release_first.wait()

    async def writer() -> None:
        async with gate.exclusive():
            writer_entered.set()
            await release_writer.wait()

    async def late_reader() -> None:
        async with gate.shared():
            late_entered.set()

    first = asyncio.create_task(first_reader())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    restore = asyncio.create_task(writer())
    await asyncio.sleep(0)
    late = asyncio.create_task(late_reader())
    await asyncio.sleep(0)

    assert not writer_entered.is_set()
    assert not late_entered.is_set()

    release_first.set()
    await asyncio.wait_for(writer_entered.wait(), timeout=1)
    assert not late_entered.is_set()

    release_writer.set()
    await asyncio.wait_for(late_entered.wait(), timeout=1)
    await asyncio.gather(first, restore, late)


async def test_same_task_reader_can_reenter_while_writer_is_waiting() -> None:
    """Nested management work cannot deadlock its own already-active read lease."""
    gate = AgentMaintenanceGate()
    writer_entered = asyncio.Event()

    async with gate.shared():
        writer = asyncio.create_task(_enter_writer(gate, writer_entered))
        await asyncio.sleep(0)
        async with gate.shared():
            pass
        assert not writer_entered.is_set()

    await asyncio.wait_for(writer_entered.wait(), timeout=1)
    await writer


async def _enter_writer(gate: AgentMaintenanceGate, entered: asyncio.Event) -> None:
    async with gate.exclusive():
        entered.set()


async def test_cancelled_writer_wait_does_not_strand_readers() -> None:
    """Cancelling a restore before exclusivity removes its writer-preference bit."""
    gate = AgentMaintenanceGate()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    late_entered = asyncio.Event()

    async def first_reader() -> None:
        async with gate.shared():
            first_entered.set()
            await release_first.wait()

    async def late_reader() -> None:
        async with gate.shared():
            late_entered.set()

    first = asyncio.create_task(first_reader())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    writer = asyncio.create_task(_enter_writer(gate, asyncio.Event()))
    await asyncio.sleep(0)
    late = asyncio.create_task(late_reader())
    await asyncio.sleep(0)
    assert not late_entered.is_set()

    writer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await writer
    # Readers are mutually compatible, so the late reader should no longer be
    # blocked merely because the cancelled writer had once been queued.
    await asyncio.wait_for(late_entered.wait(), timeout=1)

    release_first.set()
    await asyncio.gather(first, late)


async def test_cancelled_restore_finishes_before_reader_sees_state() -> None:
    """Cancellation cannot expose a half-applied multi-category restore."""
    gate = AgentMaintenanceGate()
    first_step_done = asyncio.Event()
    release_restore = asyncio.Event()
    state = ["old-memory", "old-rules"]
    observed: list[tuple[str, str]] = []

    async def restore_operation() -> str:
        state[0] = "new-memory"
        first_step_done.set()
        await release_restore.wait()
        state[1] = "new-rules"
        return "restored"

    async def observer() -> None:
        async with gate.shared():
            observed.append((state[0], state[1]))

    restore = asyncio.create_task(
        _async_run_exclusive_operation(gate, restore_operation)
    )
    await asyncio.wait_for(first_step_done.wait(), timeout=1)
    reader = asyncio.create_task(observer())
    await asyncio.sleep(0)
    assert not reader.done()

    restore.cancel()
    await asyncio.sleep(0)
    assert not restore.done()
    restore.cancel()
    await asyncio.sleep(0)
    assert not restore.done()

    release_restore.set()
    with pytest.raises(asyncio.CancelledError):
        await restore
    await reader

    assert state == ["new-memory", "new-rules"]
    assert observed == [("new-memory", "new-rules")]


async def test_restore_error_wins_over_deferred_cancellation() -> None:
    """A deterministic severe restore failure is not hidden by caller cancellation."""
    gate = AgentMaintenanceGate()
    started = asyncio.Event()
    release = asyncio.Event()

    async def failed_restore() -> None:
        started.set()
        await release.wait()
        raise BackupError(
            "Restore failed and the previous state could not be fully recovered"
        )

    restore = asyncio.create_task(_async_run_exclusive_operation(gate, failed_restore))
    await asyncio.wait_for(started.wait(), timeout=1)
    restore.cancel()
    await asyncio.sleep(0)
    assert not restore.done()
    release.set()

    with pytest.raises(BackupError, match="could not be fully recovered"):
        await restore


async def test_different_agents_do_not_share_maintenance_barrier(hass) -> None:
    """Maintenance for one agent must not serialize an unrelated agent."""
    first = get_agent_maintenance_gate(hass, "entry-1", "agent-1")
    second = get_agent_maintenance_gate(hass, "entry-1", "agent-2")
    second_entered = asyncio.Event()

    async def second_reader() -> None:
        async with second.shared():
            second_entered.set()

    async with first.exclusive():
        reader = asyncio.create_task(second_reader())
        await asyncio.wait_for(second_entered.wait(), timeout=1)
        await reader


async def test_service_proxy_waits_behind_exclusive_restore() -> None:
    """Direct service manager methods participate in the same reader boundary."""
    gate = AgentMaintenanceGate()
    called = asyncio.Event()

    class Manager:
        async def async_mutate(self) -> str:
            called.set()
            return "done"

    proxy = _SharedGateProxy(Manager(), gate)
    async with gate.exclusive():
        mutation = asyncio.create_task(proxy.async_mutate())
        await asyncio.sleep(0)
        assert not called.is_set()

    assert await mutation == "done"
    assert called.is_set()
