"""Public safety boundaries work without startup replacement of their methods."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    backup,
    delayed_tools,
    restore_recovery,
    services,
)
from custom_components.extended_openai_conversation_responses.ha_permissions import (
    bind_active_ha_context,
    get_active_ha_context,
)
from homeassistant.core import Context
from tests.test_service_handlers import _call, _memory_handlers


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize("user_id", [None, "origin"])
async def test_delayed_identity_covers_validation_and_restores_after_failure(
    hass, user_id, failure
):
    manager = delayed_tools.DelayedToolManager(hass)
    manager._records["due"] = SimpleNamespace(
        status="pending", user_id=user_id, entry_id="entry"
    )

    def resolve(_entry_id):
        assert get_active_ha_context().user_id == user_id
        raise failure()

    hass.config_entries.async_get_entry.side_effect = resolve
    caller = Context(user_id="caller")
    with bind_active_ha_context(caller):
        with pytest.raises(failure):
            await manager._async_execute_due("due")
        assert get_active_ha_context() is caller


async def test_memory_service_holds_one_lease_across_lookup_and_mutation(
    hass, monkeypatch
):
    gate = agent_maintenance.get_agent_maintenance_gate(hass, "entry", "agent")
    started = asyncio.Event()
    finish = asyncio.Event()

    async def delete(*_):
        assert gate._active_readers == 1
        started.set()
        await finish.wait()
        return 1

    manager = SimpleNamespace(async_delete=delete)
    handlers, getter = await _memory_handlers(hass, monkeypatch, manager)

    async def lookup(*_):
        assert gate._active_readers == 1
        return manager

    getter.side_effect = lookup
    async with gate.exclusive():
        task = asyncio.create_task(
            handlers[services.SERVICE_MEMORY_DELETE](
                _call(
                    {
                        "config_entry": "entry",
                        "agent_id": "agent",
                        "memory_ids": ["one"],
                    },
                    "alice",
                )
            )
        )
        await asyncio.sleep(0)
        getter.assert_not_awaited()
    await started.wait()
    assert gate._active_readers == 1
    finish.set()
    assert await task == {"deleted": 1}
    assert gate._active_readers == 0


async def test_public_restore_defers_cancellation_until_recovery_finishes(
    hass, monkeypatch
):
    gate = agent_maintenance.get_agent_maintenance_gate(hass, "entry", "agent")
    started = asyncio.Event()
    finish = asyncio.Event()

    async def recover(*_):
        assert gate._writer_active
        started.set()
        await finish.wait()
        assert gate._writer_active
        return {"status": "restored"}

    monkeypatch.setattr(restore_recovery, "async_restore_backup_recoverably", recover)
    task = asyncio.create_task(
        backup.async_restore_backup(
            hass,
            SimpleNamespace(entry_id="entry"),
            SimpleNamespace(subentry_id="agent"),
            {},
        )
    )
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert gate._writer_active
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not gate._writer_active


def test_migrated_installers_and_runtime_assignments_are_absent():
    root = Path(backup.__file__).parent
    removed = {
        "install_safety_hardening",
        "install_agent_maintenance_barrier",
        "install_restore_recovery",
        "_SharedGateProxy",
        "_install_native_tool_guards",
        "_install_delayed_permission_context",
        "_install_broadcast_state_transactions",
        "_install_backup_guards",
        "_install_service_guards",
        "_install_legacy_memory_guard",
    }
    seams = {
        "_async_execute_due",
        "execute_service",
        "add_automation",
        "get_history",
        "get_statistics",
        "async_initialize",
        "async_set_enabled",
        "async_create_backup",
        "async_restore_backup",
        "async_manage_command",
    }
    for name in (
        "safety_hardening.py",
        "agent_maintenance.py",
        "restore_recovery.py",
        "__init__.py",
    ):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert node.name not in removed
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    assert not (
                        isinstance(target, ast.Attribute) and target.attr in seams
                    )
