"""Critical disk acknowledgement and HA corruption evidence contracts."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from custom_components.extended_openai_conversation_responses.strict_store import (
    PropagatingWriteStore,
    RecoveryGuardedStore,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from homeassistant.core import CoreState


@pytest.mark.parametrize("bound", [False, True])
async def test_critical_shutdown_receipt_is_a_fresh_disk_generation(hass, bound):
    hass.state = CoreState.stopping
    store = PropagatingWriteStore(hass, 1, "shutdown-critical", atomic_writes=True)
    if bound:
        store.bind_agent("entry", "agent")
    for generation in [1, 2]:
        await store.async_save({"generation": generation})
        assert store._data is None
        assert json.loads(Path(store.path).read_text())["data"] == {
            "generation": generation
        }
        assert await PropagatingWriteStore(hass, 1, store.key).async_load() == {
            "generation": generation
        }
    assert store._unsub_final_write_listener is None


async def test_best_effort_shutdown_save_keeps_native_defer_policy(hass):
    hass.state = CoreState.stopping
    store = RecoveryGuardedStore(hass, 1, "shutdown-best-effort")
    await store.async_save({"generation": 1})
    assert not Path(store.path).exists()
    assert store._data["data"] == {"generation": 1}


@pytest.mark.parametrize("after_write", [False, True])
async def test_shutdown_failure_never_becomes_a_successful_receipt(
    hass, monkeypatch, after_write
):
    hass.state = CoreState.stopping
    store = PropagatingWriteStore(
        hass, 1, "shutdown-failure", atomic_writes=True, serialize_in_event_loop=False
    )
    write = store._write_data

    def fail(data):
        if after_write:
            write(data)
        raise OSError("private failure")

    monkeypatch.setattr(store, "_write_data", fail)
    with pytest.raises(OSError):
        await store.async_save({"generation": 1})
    assert Path(store.path).exists() is after_write


async def test_missing_active_file_with_ha_corruption_evidence_blocks_fresh_writers(
    hass,
):
    store = PropagatingWriteStore(hass, 1, "damaged.entry.agent")
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    evidence = path.with_name(path.name + ".corrupt.saved")
    evidence.write_bytes(b"PRIVATE-DAMAGED-JSON")
    for _ in range(2):
        fresh = PropagatingWriteStore(hass, 1, store.key)
        blocked = False
        try:
            await fresh.async_load()
        except StorageRepairRequired:
            blocked = True
        try:
            await fresh.async_save({"sources": []})
        except StorageRepairRequired:
            pass
        assert not path.exists()
        assert blocked
        assert not path.exists() and evidence.read_bytes() == b"PRIVATE-DAMAGED-JSON"
    # An administrator installs a valid original generation. Keep historical HA
    # evidence/notifications while accepting the independently repaired file.
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "minor_version": 1,
                "key": store.key,
                "data": {"sources": ["repaired"]},
            }
        )
    )
    repaired = PropagatingWriteStore(hass, 1, store.key)
    assert await repaired.async_load() == {"sources": ["repaired"]}
    await repaired.async_save({"sources": ["repaired", "next"]})
    assert json.loads(path.read_text())["data"]["sources"] == ["repaired", "next"]
    assert evidence.exists()


async def test_first_run_and_unrelated_corruption_evidence_are_not_damage(hass):
    store = PropagatingWriteStore(hass, 1, "fresh.entry.agent")
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(path.name + "2.corrupt.saved").write_text("unrelated")
    assert await store.async_load() is None
    await store.async_save({"records": []})
    assert json.loads(path.read_text())["data"] == {"records": []}


async def test_actual_ha_json_rejection_keeps_notification_and_blocks_empty_generation(
    hass, monkeypatch
):
    import os
    from homeassistant.helpers import issue_registry

    store = PropagatingWriteStore(hass, 1, "json-corrupt.entry.agent")
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{invalid JSON evidence")
    notify = Mock()
    monkeypatch.setattr(issue_registry, "async_create_issue", notify)
    rename = os.rename

    def portable_rename(source, destination):
        destination = Path(destination)
        rename(source, destination.with_name(destination.name.replace(":", "_")))

    monkeypatch.setattr(os, "rename", portable_rename)
    with pytest.raises(StorageRepairRequired):
        await store.async_load()
    evidence = list(path.parent.glob(path.name + ".corrupt.*"))
    assert len(evidence) == 1 and evidence[0].read_bytes() == b"{invalid JSON evidence"
    assert notify.call_count == 1
    assert notify.call_args.kwargs["translation_key"] == "storage_corruption"
    with pytest.raises(StorageRepairRequired):
        await store.async_save({"records": []})
    assert not path.exists()


async def test_cancelled_shutdown_save_settles_before_exclusive_maintenance(
    hass, monkeypatch
):
    import asyncio

    hass.state = CoreState.stopping
    store = PropagatingWriteStore(
        hass, 1, "shutdown-cancel", atomic_writes=True
    ).bind_agent("entry", "agent")
    entered, release, exported = asyncio.Event(), asyncio.Event(), asyncio.Event()
    write = store._async_write_data

    async def held(data):
        entered.set()
        await release.wait()
        await write(data)

    monkeypatch.setattr(store, "_async_write_data", held)
    save = asyncio.create_task(store.async_save({"generation": 1}))
    await entered.wait()

    async def export():
        async with store._recovery_gate.exclusive():
            assert json.loads(Path(store.path).read_text())["data"] == {"generation": 1}
            exported.set()

    maintenance = asyncio.create_task(export())
    save.cancel()
    await asyncio.sleep(0)
    save.cancel()
    await asyncio.sleep(0)
    assert not save.done() and not exported.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await save
    await maintenance
    assert exported.is_set()


async def test_knowledge_shutdown_acknowledgement_survives_fresh_manager_and_next_create(
    hass,
):
    from custom_components.extended_openai_conversation_responses.knowledge import (
        HomeAssistantKnowledgeStorage,
        KnowledgeLibrary,
    )

    hass.state = CoreState.stopping
    storage = HomeAssistantKnowledgeStorage(hass, "entry", "agent")
    library = KnowledgeLibrary(storage)
    await library.async_initialize()
    first = await library.async_create("First", "", "First stored fact")
    fresh = KnowledgeLibrary(HomeAssistantKnowledgeStorage(hass, "entry", "agent"))
    await fresh.async_initialize()
    assert [
        source["source_id"] for source in (await fresh.async_backup_data())["sources"]
    ] == [first.source_id]
    second = await fresh.async_create("Next", "", "Next stored fact")
    disk = json.loads(Path(storage._store.path).read_text())["data"]
    assert {source["source_id"] for source in disk["sources"]} == {
        first.source_id,
        second.source_id,
    }
