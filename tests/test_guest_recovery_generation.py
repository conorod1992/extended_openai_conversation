"""Recovery reads cannot replace a later acknowledged Guest generation."""

import asyncio
from datetime import timedelta

import pytest

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.const import (
    CONF_GUEST_MODE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
    async_get_guest_mode,
)
from homeassistant.util import dt as dt_util
from tests.test_guest_websocket_storage_outcomes import send
from tests.test_save_validation_reuse import _hass_with_agent


@pytest.mark.parametrize("cancel_recovery", [None, "waiting", "reading"])
async def test_recovery_does_not_erase_queued_restriction_or_weaken_next_action(
    hass,
    entry_agent,
    monkeypatch,
    cancel_recovery,
):
    _, entry, subentry = _hass_with_agent()
    monkeypatch.setattr(ui, "entry_and_agent", lambda *_: (entry, subentry))
    manager = await async_get_guest_mode(hass, entry.entry_id, subentry.subentry_id)
    await manager.async_disable_trusted()
    subentry.data = {**subentry.data, CONF_GUEST_MODE_ENABLED: True}
    entry_agent.entry = entry
    entry_agent.subentry = subentry
    entry_agent._guest_mode = manager
    first_started, release_first, second_admitted, second_started, release_second = (
        asyncio.Event() for _ in range(5)
    )
    read_started, release_read, recovery_waiting = (asyncio.Event() for _ in range(3))
    save = manager._store.async_save
    load = manager._store.async_load
    get = ui.async_get_guest_mode
    saves = gets = reads = 0
    recovery = None

    async def observed_get(*args):
        nonlocal gets
        result = await get(*args)
        gets += 1
        if gets == 2:
            second_admitted.set()
        return result

    async def controlled_save(data):
        nonlocal saves
        saves += 1
        if saves == 1:
            first_started.set()
            await release_first.wait()
            raise OSError("write failed")
        if saves == 2:
            second_started.set()
            await release_second.wait()
        await save(data)

    async def controlled_load():
        nonlocal reads
        reads += 1
        if reads == 1:
            raise OSError("reconciliation read failed")
        snapshot = await load()
        read_started.set()
        await release_read.wait()
        return snapshot

    # Observe admission only to coordinate both implementations without sleeps:
    # the old recovery starts its read; a serialized recovery waits on the writer.
    acquire = manager._mutation_lock.acquire

    async def observed_acquire():
        if asyncio.current_task() is recovery:
            recovery_waiting.set()
        return await acquire()

    monkeypatch.setattr(ui, "async_get_guest_mode", observed_get)
    monkeypatch.setattr(manager._store, "async_save", controlled_save)
    monkeypatch.setattr(manager._store, "async_load", controlled_load)
    monkeypatch.setattr(manager._mutation_lock, "acquire", observed_acquire)
    tasks = []
    try:
        first = asyncio.create_task(send(hass, entry, subentry, "update"))
        tasks.append(first)
        await first_started.wait()
        second = asyncio.create_task(send(hass, entry, subentry, "update"))
        tasks.append(second)
        await second_admitted.wait()
        release_first.set()
        rejected = await first
        assert rejected.send_error.call_args.args[1] == "eoai_mutation_uncertain"
        await second_started.wait()
        recovery = asyncio.create_task(get(hass, entry.entry_id, subentry.subentry_id))
        tasks.append(recovery)
        signals = [
            asyncio.create_task(event.wait())
            for event in (read_started, recovery_waiting)
        ]
        tasks.extend(signals)
        await asyncio.wait(signals, return_when=asyncio.FIRST_COMPLETED)
        if cancel_recovery == "waiting":
            recovery.cancel()
            with pytest.raises(asyncio.CancelledError):
                await recovery
        release_second.set()
        acknowledged = await second
        acknowledged.send_error.assert_not_called()
        assert acknowledged.send_result.call_args.args[1]["status"]["currently_active"]
        if cancel_recovery != "waiting":
            await read_started.wait()
        if cancel_recovery == "reading":
            recovery.cancel()
            with pytest.raises(asyncio.CancelledError):
                await recovery
        release_read.set()
        await get(hass, entry.entry_id, subentry.subentry_id)
        if not cancel_recovery:
            await recovery
        recovered_active = manager.is_active()
        # A normal monotonic restriction must never turn an acknowledged
        # indefinite restriction into an expiring one because of a stale read.
        await entry_agent._async_execute_guest_mode_tool(
            {"active_until": (dt_util.utcnow() + timedelta(hours=1)).isoformat()}
        )
        fresh = GuestModeManager(hass, entry.entry_id, subentry.subentry_id)
        await fresh.async_initialize()
        assert (recovered_active, fresh.is_active(), fresh.schedule.active_until) == (
            True,
            True,
            None,
        )
        assert saves == 3
    finally:
        for event in (
            release_first,
            release_second,
            release_read,
            recovery_waiting,
            read_started,
        ):
            event.set()
        await asyncio.gather(*tasks, return_exceptions=True)
