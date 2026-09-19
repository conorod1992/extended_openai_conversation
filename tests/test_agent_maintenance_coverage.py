"""Focused residual coverage for the per-agent maintenance boundary."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import agent_maintenance


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
