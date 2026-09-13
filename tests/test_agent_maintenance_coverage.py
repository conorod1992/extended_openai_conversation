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


async def test_exclusive_operation_propagates_operation_cancellation_and_releases_gate() -> None:
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


def test_shared_gate_proxy_passes_through_non_async_attributes() -> None:
    target = SimpleNamespace(label="memory-manager")
    proxy = agent_maintenance._SharedGateProxy(
        target, agent_maintenance.AgentMaintenanceGate()
    )

    assert proxy.label == "memory-manager"


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


async def test_conversation_guard_bypasses_partial_entities_and_gates_real_identity(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import conversation

    calls: list[str] = []
    gate = RecordingGate()

    async def original(entity, marker):
        calls.append(marker)
        return marker

    monkeypatch.setattr(conversation.ExtendedOpenAIAgentEntity, "_async_process", original)
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_args: gate
    )

    agent_maintenance._install_conversation_guard()
    wrapped = conversation.ExtendedOpenAIAgentEntity._async_process

    partial = SimpleNamespace()
    assert await wrapped(partial, "partial") == "partial"
    assert gate.shared_entries == 0

    complete = SimpleNamespace(
        hass=object(),
        entry=SimpleNamespace(entry_id="entry"),
        subentry=SimpleNamespace(subentry_id="agent"),
    )
    assert await wrapped(complete, "complete") == "complete"
    assert gate.shared_entries == 1
    assert calls == ["partial", "complete"]


async def test_backup_guards_use_exclusive_gate_and_update_management_aliases(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import backup, management_ui

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

    monkeypatch.setattr(backup, "async_create_backup", original_create)
    monkeypatch.setattr(backup, "async_collect_backup_snapshot", collect)
    monkeypatch.setattr(backup, "finalize_backup_snapshot", finalize)
    monkeypatch.setattr(backup, "async_restore_backup", original_restore)
    monkeypatch.setattr(management_ui, "async_create_backup", original_create)
    monkeypatch.setattr(management_ui, "async_restore_backup", original_restore)
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_args: gate
    )

    agent_maintenance._install_backup_guards()
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


async def test_management_guard_bypasses_owned_paths_and_gates_agent_commands(
    monkeypatch,
) -> None:
    from custom_components.extended_openai_conversation_responses import management_ui

    gate = RecordingGate()
    calls: list[dict] = []

    async def original(_hass, _user_id, _is_admin, message):
        calls.append(message)
        return {"action": message.get("action")}

    monkeypatch.setattr(management_ui, "async_management_command", original)
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_args: gate
    )
    agent_maintenance._install_management_guard()
    command = management_ui.async_management_command

    for message in (
        {"action": "agents"},
        {"section": "backup", "action": "create"},
        {"section": "request_rules", "action": "test"},
        {"section": "diagnostics", "action": "test_agent"},
        {"section": "overview", "action": "get"},
    ):
        await command(object(), "user", True, message)

    assert gate.shared_entries == 0

    await command(
        object(),
        "user",
        True,
        {
            "section": "memories",
            "action": "list",
            "entry_id": "entry",
            "subentry_id": "agent",
        },
    )
    assert gate.shared_entries == 1
    assert len(calls) == 6


async def test_legacy_memory_guard_bypass_and_normal_gate(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import memory_ui

    gate = RecordingGate()

    async def original(_hass, _user_id, message):
        return {"action": message.get("action")}

    monkeypatch.setattr(memory_ui, "async_manage_command", original)
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_args: gate
    )
    agent_maintenance._install_legacy_memory_guard()
    command = memory_ui.async_manage_command

    await command(object(), "user", {"action": "agents"})
    await command(object(), "user", {"action": "test_agent"})
    await command(object(), "user", {"action": "list", "entry_id": "entry"})
    assert gate.shared_entries == 0

    await command(
        object(),
        "user",
        {"action": "list", "entry_id": "entry", "subentry_id": "agent"},
    )
    assert gate.shared_entries == 1


async def test_service_guards_gate_getters_proxy_methods_and_tool_state(monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses import services

    gate = RecordingGate()
    mutations: list[str] = []

    class Manager:
        label = "manager"

        async def async_mutate(self, value: str) -> str:
            mutations.append(value)
            return value

    manager = Manager()

    async def get_memory(_hass, _entry_id, _subentry_id):
        return manager

    async def get_guest(_hass, _entry_id, _subentry_id):
        return manager

    async def set_tools(_hass, _entry_id, _agent_reference, names, enabled):
        mutations.append(f"{names[0]}:{enabled}")

    monkeypatch.setattr(services, "async_get_memory", get_memory)
    monkeypatch.setattr(services, "async_get_guest_mode", get_guest)
    monkeypatch.setattr(services, "async_set_function_tools_enabled", set_tools)
    monkeypatch.setattr(
        services,
        "resolve_memory_agent",
        lambda _hass, _entry_id, _reference: (object(), "agent"),
    )
    monkeypatch.setattr(
        agent_maintenance, "get_agent_maintenance_gate", lambda *_args: gate
    )

    agent_maintenance._install_service_guards()

    memory_proxy = await services.async_get_memory(object(), "entry", "agent")
    guest_proxy = await services.async_get_guest_mode(object(), "entry", "agent")
    assert memory_proxy.label == "manager"
    assert guest_proxy.label == "manager"
    assert await memory_proxy.async_mutate("memory") == "memory"
    assert await guest_proxy.async_mutate("guest") == "guest"
    await services.async_set_function_tools_enabled(
        object(), "entry", "reference", ["demo"], False
    )

    # Two getter leases + two proxied method leases + one tool-state lease.
    assert gate.shared_entries == 5
    assert mutations == ["memory", "guest", "demo:False"]


def test_install_agent_maintenance_barrier_is_idempotent(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(agent_maintenance, "_INSTALLED", False)
    monkeypatch.setattr(
        agent_maintenance,
        "_install_conversation_guard",
        lambda: calls.append("conversation"),
    )
    monkeypatch.setattr(
        agent_maintenance, "_install_backup_guards", lambda: calls.append("backup")
    )
    monkeypatch.setattr(
        agent_maintenance,
        "_install_management_guard",
        lambda: calls.append("management"),
    )
    monkeypatch.setattr(
        agent_maintenance,
        "_install_legacy_memory_guard",
        lambda: calls.append("legacy"),
    )
    monkeypatch.setattr(
        agent_maintenance, "_install_service_guards", lambda: calls.append("services")
    )

    agent_maintenance.install_agent_maintenance_barrier()
    agent_maintenance.install_agent_maintenance_barrier()

    assert calls == ["conversation", "backup", "management", "legacy", "services"]
