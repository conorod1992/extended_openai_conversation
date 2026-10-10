"""Request Rule concurrency revisions survive reloads and transactional failures."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import request_rules as rr
from tests.test_request_rules_management import MemoryStore, local_rule


async def test_saved_revision_survives_manager_reload_and_accepts_next_edit():
    store = MemoryStore()
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    await manager.async_create(local_rule())
    revision = manager.revision()
    backup = await manager.async_backup_data()

    reloaded = rr.RequestRules(store)
    await reloaded.async_initialize()
    assert reloaded.revision() == revision
    assert await reloaded.async_backup_data() == backup
    assert "revision_generation" not in backup
    await reloaded.async_create(local_rule(rule_id="next"), expected_revision=revision)
    assert reloaded.revision() != revision


async def test_aba_changes_still_reject_old_writer_after_reload():
    store = MemoryStore()
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    original = manager.revision()
    await manager.async_set_defaults({**rr.DEFAULT_MATCHING, "fuzzy": True})

    manager = rr.RequestRules(store)
    await manager.async_initialize()
    await manager.async_set_defaults(rr.DEFAULT_MATCHING)

    manager = rr.RequestRules(store)
    await manager.async_initialize()
    with pytest.raises(ValueError, match="another tab"):
        await manager.async_create(local_rule(), expected_revision=original)
    assert store.saves == 2


@pytest.mark.parametrize("write_reached_disk", [False, True])
async def test_failed_write_reconciles_durable_revision(write_reached_disk):
    store = MemoryStore()
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    await manager.async_create(local_rule())
    before = manager.revision()
    save = store.async_save

    async def fail(data):
        if write_reached_disk:
            await save(data)
        raise OSError("lost write acknowledgement")

    store.async_save = fail
    with pytest.raises(OSError, match="acknowledgement"):
        await manager.async_set_defaults({**rr.DEFAULT_MATCHING, "fuzzy": True})
    store.async_save = save
    reloaded = rr.RequestRules(store)
    await reloaded.async_initialize()
    assert manager.revision() == reloaded.revision()
    assert (manager.revision() != before) is write_reached_disk
    if write_reached_disk:
        with pytest.raises(ValueError, match="another tab"):
            await manager.async_create(
                local_rule(rule_id="next"), expected_revision=before
            )
    else:
        await manager.async_create(local_rule(rule_id="next"), expected_revision=before)


@pytest.mark.parametrize("generation", [-1, True, "bad"])
async def test_invalid_stored_generation_is_repaired_once(generation):
    store = MemoryStore({"revision_generation": generation})
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    assert type(store.data["revision_generation"]) is int
    assert store.data["revision_generation"] >= 1
    revision = manager.revision()
    reloaded = rr.RequestRules(store)
    await reloaded.async_initialize()
    assert reloaded.revision() == revision
    assert store.saves == 1


async def test_pending_save_does_not_publish_new_matching_generation():
    import asyncio

    store = MemoryStore()
    manager = rr.RequestRules(store)
    await manager.async_initialize()
    original_generation = manager._generation
    entered, release = asyncio.Event(), asyncio.Event()
    save = store.async_save

    async def delayed(data):
        entered.set()
        await release.wait()
        await save(deepcopy(data))

    store.async_save = delayed
    task = asyncio.create_task(manager.async_create(local_rule()))
    await entered.wait()
    assert manager._generation == original_generation
    assert manager._committed_matching_snapshot.deterministic == ()
    release.set()
    await task
    assert manager._generation == original_generation + 1
