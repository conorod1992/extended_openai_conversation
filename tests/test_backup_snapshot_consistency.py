"""Regression tests for point-in-time full-backup snapshots."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    backup,
    management_ui,
)


async def test_backup_excludes_mutations_only_while_collecting_snapshot(
    hass, monkeypatch
) -> None:
    """Ordinary work cannot interleave stores, but serialization is not exclusive."""
    entry = SimpleNamespace(entry_id="entry-1")
    subentry = SimpleNamespace(subentry_id="agent-1")
    gate = agent_maintenance.get_agent_maintenance_gate(
        hass, entry.entry_id, subentry.subentry_id
    )
    collection_started = asyncio.Event()
    finish_collection = asyncio.Event()
    shared_entered = asyncio.Event()
    finalized = asyncio.Event()

    async def original_create(*_args, **_kwargs):
        raise AssertionError("guarded backup must use split snapshot collection")

    async def already_guarded_restore(*_args, **_kwargs):
        return {"status": "unused"}

    already_guarded_restore._extended_openai_maintenance_gate = True

    async def collect_snapshot(*_args, **_kwargs):
        assert gate._writer_active is True
        collection_started.set()
        await finish_collection.wait()
        assert gate._writer_active is True
        return {
            "agent": {"title": "Jarvis"},
            "created_at": "2026-09-06T01:00:00+00:00",
        }

    def finalize_snapshot(snapshot):
        assert snapshot["agent"]["title"] == "Jarvis"
        assert gate._writer_active is False
        finalized.set()
        return {"document": snapshot}

    monkeypatch.setattr(backup, "async_create_backup", original_create)
    monkeypatch.setattr(management_ui, "async_create_backup", original_create)
    monkeypatch.setattr(backup, "async_restore_backup", already_guarded_restore)
    monkeypatch.setattr(management_ui, "async_restore_backup", already_guarded_restore)
    monkeypatch.setattr(backup, "async_collect_backup_snapshot", collect_snapshot)
    monkeypatch.setattr(backup, "finalize_backup_snapshot", finalize_snapshot)

    agent_maintenance._install_backup_guards()
    guarded_create = backup.async_create_backup

    async def ordinary_mutation() -> None:
        async with gate.shared():
            shared_entered.set()

    backup_task = asyncio.create_task(guarded_create(hass, entry, subentry))
    await collection_started.wait()
    mutation_task = asyncio.create_task(ordinary_mutation())
    await asyncio.sleep(0)
    assert not shared_entered.is_set()

    finish_collection.set()
    result = await backup_task
    await mutation_task

    assert finalized.is_set()
    assert shared_entered.is_set()
    assert result["document"]["agent"]["title"] == "Jarvis"
