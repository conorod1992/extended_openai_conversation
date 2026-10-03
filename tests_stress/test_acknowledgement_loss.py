"""Nightly real-Store commits whose caller loses the acknowledgement."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    HomeAssistantKnowledgeStorage,
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION as RULES_VERSION,
    RequestRules,
    RequestRuleStore,
)
from homeassistant.core import HomeAssistant
from tests_stress.conftest import record
from tests_stress.test_os_storage_faults import real_store_io as real_store_io


@pytest.mark.parametrize(
    "owner", ("persistent_memory", "request_rules", "knowledge", "delayed_tools")
)
@pytest.mark.usefixtures("real_store_io")
async def test_atomic_commit_survives_lost_acknowledgement(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    stress_trace: list[dict],
    owner: str,
) -> None:
    """Cancellation after HA's atomic write must retain one authoritative object."""
    entry_id, subentry_id = "ack-entry", "ack-agent"
    if owner == "persistent_memory":
        storage = HomeAssistantMemoryStorage(hass, entry_id, subentry_id)
        manager = PersistentMemory(storage)
        await manager.async_initialize()
        store = storage._store
        mutate = manager.async_add(
            "ack-user", "Committed despite lost reply", "ack", "explicit"
        )
    elif owner == "request_rules":
        store = RequestRuleStore(
            hass, RULES_VERSION, "extended_openai_conversation.ack_rules"
        )
        manager = RequestRules(store)
        await manager.async_initialize()
        before_revision = manager.revision()
        mutate = manager.async_set_groups(
            [{"id": "ack-group", "name": "Acknowledgement group"}],
            expected_revision=before_revision,
        )
    elif owner == "knowledge":
        storage = HomeAssistantKnowledgeStorage(hass, entry_id, subentry_id)
        manager = KnowledgeLibrary(storage)
        await manager.async_initialize()
        store = storage._store
        mutate = manager.async_create(
            "Acknowledgement source", "Committed source", "Persisted content"
        )
    else:
        manager = DelayedToolManager(hass)
        await manager.async_setup()
        store = manager._store
        entity = SimpleNamespace(
            entry=SimpleNamespace(entry_id=entry_id),
            subentry=SimpleNamespace(subentry_id=subentry_id),
        )
        mutate = manager.async_schedule(
            entity, "scheduled_ack_tool", {"delay": {"hours": 1}}, None
        )

    committed = asyncio.Event()
    release_ack = asyncio.Event()
    original_write = store._async_write_data

    async def write_then_lose_ack(data: dict[str, Any]) -> None:
        await original_write(data)
        committed.set()
        await release_ack.wait()

    with monkeypatch.context() as patch:
        patch.setattr(store, "_async_write_data", write_then_lose_ack)
        task = asyncio.create_task(mutate)
        await asyncio.wait_for(committed.wait(), timeout=10)
        assert Path(store.path).is_file()
        task.cancel()
        release_ack.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    if owner == "persistent_memory":
        runtime = await manager.async_list("ack-user")
        reloaded = PersistentMemory(
            HomeAssistantMemoryStorage(hass, entry_id, subentry_id)
        )
        await reloaded.async_initialize()
        disk = await reloaded.async_list("ack-user")
        assert len(runtime) == len(disk) == 1
        assert runtime[0].memory_id == disk[0].memory_id
        retry = await manager.async_add(
            "ack-user", "Committed despite lost reply", "ack", "explicit"
        )
        assert retry["status"] == "duplicate"
        assert retry["memory"]["memory_id"] == disk[0].memory_id
    elif owner == "request_rules":
        reloaded = RequestRules(
            RequestRuleStore(
                hass, RULES_VERSION, "extended_openai_conversation.ack_rules"
            )
        )
        await reloaded.async_initialize()
        assert (
            manager.snapshot()["groups"]
            == reloaded.snapshot()["groups"]
            == [{"id": "ack-group", "name": "Acknowledgement group"}]
        )
        assert manager.revision() != before_revision
        assert reloaded.revision() != before_revision
        with pytest.raises(ValueError, match="changed in another tab"):
            await manager.async_set_groups([], expected_revision=before_revision)
        with pytest.raises(ValueError, match="changed in another tab"):
            await reloaded.async_set_groups([], expected_revision=before_revision)
    elif owner == "knowledge":
        reloaded = KnowledgeLibrary(
            HomeAssistantKnowledgeStorage(hass, entry_id, subentry_id)
        )
        await reloaded.async_initialize()
        runtime = await manager.async_list()
        disk = await reloaded.async_list()
        assert len(runtime) == len(disk) == 1
        assert runtime[0]["source_id"] == disk[0]["source_id"]
        assert (
            await reloaded.async_get(disk[0]["source_id"])
        ).content == "Persisted content"
    else:
        reloaded = DelayedToolManager(hass)
        await reloaded.async_setup()
        assert len(manager._records) == len(reloaded._records) == 1
        assert set(manager._records) == set(reloaded._records)
        assert next(iter(reloaded._records.values())).tool_name == "scheduled_ack_tool"

    record(
        stress_trace,
        "acknowledgement_loss",
        owner=owner,
        seam="real_ha_atomic_write_after_commit",
        runtime_disk_converged=True,
        duplicate_objects=False,
    )


@pytest.mark.parametrize(
    ("schedule", "commit_first", "cancel_first", "cancel_waiter"),
    [
        ("fail-before-then-retry", False, False, False),
        ("fail-after-commit-then-retry", True, False, False),
        ("cancel-after-commit-then-retry", True, True, False),
        ("fail-before-cancel-waiter-then-recover", False, False, True),
    ],
)
@pytest.mark.usefixtures("real_store_io")
async def test_compound_memory_write_schedules_converge_retained_runtime_and_disk(
    hass,
    monkeypatch,
    stress_trace,
    schedule,
    commit_first,
    cancel_first,
    cancel_waiter,
):
    """Bounded compound schedules must converge without lost or invented facts."""
    entry_id = f"compound-entry-{schedule}"
    subentry_id = "compound-agent"
    storage = HomeAssistantMemoryStorage(hass, entry_id, subentry_id)
    manager = PersistentMemory(storage)
    await manager.async_initialize()
    store = storage._store
    await manager.async_add(
        "compound-owner", "BASELINE_COMPOUND_FACT", "compound", "explicit"
    )

    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    writes = 0
    original_write = store._async_write_data

    async def scheduled_write(data):
        nonlocal writes
        writes += 1
        if writes != 2:
            # Write 1 is the baseline above. Only the first scheduled mutation is
            # faulted; later queued/recovery writes use the ordinary HA Store path.
            return await original_write(data)
        first_entered.set()
        if commit_first:
            await original_write(data)
        await release_first.wait()
        if cancel_first:
            # Cancellation is delivered by the caller after the durable boundary.
            await asyncio.sleep(0)
            return
        raise OSError(f"controlled compound write failure: {schedule}")

    async def add(content):
        return await manager.async_add(
            "compound-owner", content, "compound", "explicit"
        )

    first = second = None
    with monkeypatch.context() as patch:
        patch.setattr(store, "_async_write_data", scheduled_write)
        first = asyncio.create_task(add("FIRST_COMPOUND_FACT"))
        await asyncio.wait_for(first_entered.wait(), 10)
        second = asyncio.create_task(add("SECOND_COMPOUND_FACT"))
        await asyncio.sleep(0)
        assert not second.done(), "second mutation must wait for first transaction ownership"
        if cancel_waiter:
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            second = None
        if cancel_first:
            first.cancel()
        release_first.set()
        first_outcome = (await asyncio.gather(first, return_exceptions=True))[0]

    if cancel_first:
        assert isinstance(first_outcome, asyncio.CancelledError)
    else:
        assert isinstance(first_outcome, OSError)

    if second is not None:
        second_result = await asyncio.wait_for(second, 10)
        assert second_result["status"] == "created"

    # Always perform a later healthy mutation through the same retained manager.
    third = await add("THIRD_COMPOUND_FACT")
    assert third["status"] == "created"

    runtime = await manager.async_list("compound-owner", limit=100)
    reloaded = PersistentMemory(
        HomeAssistantMemoryStorage(hass, entry_id, subentry_id)
    )
    await reloaded.async_initialize()
    durable = await reloaded.async_list("compound-owner", limit=100)
    runtime_contents = {item.content for item in runtime}
    durable_contents = {item.content for item in durable}
    assert runtime_contents == durable_contents
    assert "BASELINE_COMPOUND_FACT" in durable_contents
    assert "THIRD_COMPOUND_FACT" in durable_contents
    assert ("FIRST_COMPOUND_FACT" in durable_contents) is commit_first
    assert ("SECOND_COMPOUND_FACT" in durable_contents) is (not cancel_waiter)
    assert len(runtime) == len(durable) == 2 + int(commit_first) + int(not cancel_waiter)

    record(
        stress_trace,
        "summary",
        compound_persistence_schedules=1,
        compound_retained_runtime_recoveries=1,
        compound_cancelled_waiters=int(cancel_waiter),
        compound_committed_uncertain_writes=int(commit_first),
    )
