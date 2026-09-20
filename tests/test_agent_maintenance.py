"""Concurrency regressions for per-agent backup restore quiescence."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import agent_maintenance
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    AgentMaintenanceGate,
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


async def test_child_task_reader_reenters_parent_lease_while_writer_waits() -> None:
    """HA Script child work inherits the logical reader instead of deadlocking."""
    gate = AgentMaintenanceGate()
    writer_entered = asyncio.Event()
    child_entered = asyncio.Event()

    async with gate.shared():
        writer = asyncio.create_task(_enter_writer(gate, writer_entered))
        await asyncio.sleep(0)

        async def child_reader() -> None:
            async with gate.shared():
                child_entered.set()

        child = asyncio.create_task(child_reader())
        await asyncio.wait_for(child_entered.wait(), timeout=1)
        await child
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


async def test_service_lease_waits_behind_exclusive_restore() -> None:
    """Direct service manager methods participate in the same reader boundary."""
    gate = AgentMaintenanceGate()
    called = asyncio.Event()

    class Manager:
        async def async_mutate(self) -> str:
            called.set()
            return "done"

    manager = Manager()

    async def service_operation():
        async with gate.shared():
            return await manager.async_mutate()

    async with gate.exclusive():
        mutation = asyncio.create_task(service_operation())
        await asyncio.sleep(0)
        assert not called.is_set()

    assert await mutation == "done"
    assert called.is_set()


class RecordingGate:
    """Small gate stand-in that records shared/exclusive leases."""

    def __init__(self) -> None:
        self.shared_entries = 0
        self.exclusive_entries = 0

    @asynccontextmanager
    async def shared(self):
        self.shared_entries += 1
        yield

    @asynccontextmanager
    async def exclusive(self):
        self.exclusive_entries += 1
        yield


async def test_exclusive_operation_propagates_operation_cancellation_and_releases_gate() -> (
    None
):
    gate = agent_maintenance.AgentMaintenanceGate()

    async def cancelled_operation() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await agent_maintenance._async_run_exclusive_operation(
            gate, cancelled_operation
        )

    async with asyncio.timeout(1):
        async with gate.shared():
            pass


def test_gate_registry_reuses_exact_agent_gate(hass) -> None:
    first = agent_maintenance.get_agent_maintenance_gate(hass, "entry", "agent")
    second = agent_maintenance.get_agent_maintenance_gate(hass, "entry", "agent")
    other = agent_maintenance.get_agent_maintenance_gate(hass, "entry", "other")

    assert first is second
    assert other is not first


@pytest.mark.parametrize(
    ("message", "owns_gate"),
    [
        ({"section": "backup", "action": "create"}, True),
        ({"section": "request_rules", "action": "test"}, True),
        ({"section": "diagnostics", "action": "test_agent"}, True),
        ({"section": "request_rules", "action": "list"}, False),
        ({"action": "overview"}, False),
    ],
)
def test_management_operation_gate_ownership_matrix(message, owns_gate) -> None:
    assert agent_maintenance._management_operation_owns_its_gate(message) is owns_gate


async def test_conversation_lease_bypasses_partial_entities_and_gates_real_identity(
    monkeypatch,
) -> None:
    gate = RecordingGate()
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_: gate
    )
    async with agent_maintenance.conversation_request_lease(SimpleNamespace()):
        assert gate.shared_entries == 0
    complete = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )
    async with agent_maintenance.conversation_request_lease(complete):
        assert gate.shared_entries == 1


async def test_backup_guards_use_exclusive_gate_and_update_management_aliases(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        backup,
        management_ui,
        restore_recovery,
    )

    gate = RecordingGate()
    events: list[str] = []

    async def original_create(_hass, _entry, _subentry):
        raise AssertionError("guarded create should use collect/finalize path")

    async def collect(_hass, _entry, _subentry):
        events.append("collect")
        return {"snapshot": True}

    def finalize(snapshot):
        events.append("finalize")
        return {"finalized": snapshot}

    async def original_restore(_hass, _entry, _subentry, value):
        events.append("restore")
        return {"restored": value}

    monkeypatch.setattr(backup, "async_collect_backup_snapshot", collect)
    monkeypatch.setattr(backup, "finalize_backup_snapshot", finalize)
    monkeypatch.setattr(
        restore_recovery, "async_restore_backup_recoverably", original_restore
    )
    monkeypatch.setattr(backup, "get_agent_maintenance_gate", lambda *_args: gate)

    entry = SimpleNamespace(entry_id="entry")
    subentry = SimpleNamespace(subentry_id="agent")

    created = await backup.async_create_backup(object(), entry, subentry)
    restored = await backup.async_restore_backup(object(), entry, subentry, {"x": 1})

    assert created == {"finalized": {"snapshot": True}}
    assert restored == {"restored": {"x": 1}}
    assert events == ["collect", "finalize", "restore"]
    assert gate.exclusive_entries == 2
    assert management_ui.async_create_backup is backup.async_create_backup
    assert management_ui.async_restore_backup is backup.async_restore_backup


async def test_management_lease_bypasses_owned_paths_and_gates_agent_commands(
    monkeypatch,
):
    gate = RecordingGate()
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_: gate
    )
    for message in (
        {"action": "agents"},
        {"section": "backup", "action": "create"},
        {"section": "request_rules", "action": "test"},
        {"section": "diagnostics", "action": "test_agent"},
        {"section": "overview", "action": "get"},
    ):
        async with agent_maintenance.management_command_lease(object(), message):
            assert gate.shared_entries == 0
    async with agent_maintenance.management_command_lease(
        object(),
        {
            "section": "memories",
            "action": "list",
            "entry_id": "entry",
            "subentry_id": "agent",
        },
    ):
        assert gate.shared_entries == 1
