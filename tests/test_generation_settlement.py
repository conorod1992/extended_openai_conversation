"""Authoritative generations survive cancellation without claiming outer effects."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.execution_outcomes import (
    execution_attempt,
    execution_completed,
    execution_started,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    _async_settle_transactional_save,
)
from tests.test_function_tool_error_recovery import _call
from tests.test_quiet_hours_runtime import _config
from tests.test_review_quiet_recovery import _manager, timers  # noqa: F401


@pytest.mark.parametrize("cancel_during", ["write", "readback"])
@pytest.mark.parametrize("readback_fails", [False, True])
async def test_cancelled_transaction_settles_write_and_readback_without_background_error(
    cancel_during, readback_fails,
):
    write_started, finish_write = asyncio.Event(), asyncio.Event()
    readback_started, finish_readback = asyncio.Event(), asyncio.Event()
    lock = asyncio.Lock()
    state = {"generation": "candidate", "available": True}
    write_error = OSError("commit acknowledgement lost")
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    reported = []

    async def save():
        write_started.set()
        await finish_write.wait()
        raise write_error

    async def readback():
        readback_started.set()
        await finish_readback.wait()
        if readback_fails:
            raise OSError("recovery could not read the store")
        state["generation"] = "durable"

    def invalidate():
        state["available"] = False

    async def update():
        async with lock:
            await _async_settle_transactional_save(
                save(),
                invalidate,
                lambda: None,
                readback,
                invalidate,
                record_execution=False,
            )

    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    task = asyncio.create_task(update())
    try:
        await asyncio.wait_for(write_started.wait(), 5)
        if cancel_during == "write":
            task.cancel("write")
            await asyncio.sleep(0)
            assert lock.locked() and not task.done()
        finish_write.set()
        await asyncio.wait_for(readback_started.wait(), 5)
        for message in ("readback", "repeated cancellation"):
            task.cancel(message)
            await asyncio.sleep(0)
            assert lock.locked() and not task.done()
        assert state == {"generation": "candidate", "available": True}

        finish_readback.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await asyncio.wait_for(task, 5)
        assert raised.value.args == (cancel_during,)
        assert raised.value.__cause__ is write_error
        if readback_fails:
            assert state == {"generation": "candidate", "available": False}
        else:
            assert state == {"generation": "durable", "available": True}
        assert not lock.locked()
        await asyncio.sleep(0)
        assert reported == []
    finally:
        finish_write.set()
        finish_readback.set()
        await asyncio.gather(task, return_exceptions=True)
        loop.set_exception_handler(previous_handler)


async def test_internal_save_neither_starts_nor_settles_enclosing_effect():
    entity = SimpleNamespace()
    call = _call("outer", "effect", {})
    tool = {"spec": {"name": "effect", "parameters": {"type": "object"}}}
    with execution_attempt(entity, call, tool):
        receipt = execution_started()
        await _async_settle_transactional_save(
            AsyncMock()(),
            lambda: None,
            lambda: None,
            None,
            lambda: None,
            record_execution=False,
        )
        execution_completed(receipt)
    assert entity._execution_tool_outcomes[id(call)] == "completed"


@pytest.mark.parametrize("committed", [False, True])
async def test_control_save_recovers_configuration_and_ownership_together(
    hass, monkeypatch, timers, committed
):
    manager = await _manager(hass, monkeypatch, True)
    persisted = {"config": manager.config.as_dict(), "active": deepcopy(manager.active)}
    manager._config = _config(enabled=False)
    manager._active["controls"]["media_player.bedroom"]["restoration_pending"] = True
    first = True

    async def save(data):
        nonlocal first, persisted
        if first:
            first = False
            if committed:
                persisted = deepcopy(data)
            raise OSError("control write acknowledgement failed")
        persisted = deepcopy(data)

    manager._store = SimpleNamespace(
        async_save=save, async_load=AsyncMock(side_effect=lambda: deepcopy(persisted))
    )
    hass.services.async_call.reset_mock()
    with pytest.raises(OSError, match="control write"):
        await manager._async_save_control_state_locked()
    assert manager.config.as_dict() == persisted["config"]
    assert manager.active == manager._normalize_active(persisted["active"])
    hass.services.async_call.assert_not_awaited()
    await manager.async_reconcile()
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == (
        0.8 if committed else 0.2
    )


async def test_quiet_hours_readback_cancellation_restores_before_returning(
    hass, monkeypatch, timers
):
    manager = await _manager(hass, monkeypatch, True)
    entered, finish = asyncio.Event(), asyncio.Event()
    persisted = {}
    first = True

    async def save(data):
        nonlocal first
        persisted.update(deepcopy(data))
        if first:
            first = False
            raise OSError("configuration acknowledgement lost")

    async def load():
        entered.set()
        await finish.wait()
        return deepcopy(persisted)

    manager._store = SimpleNamespace(async_save=save, async_load=load)
    task = asyncio.create_task(
        manager.async_update_config(_config(enabled=False).as_dict())
    )
    await entered.wait()
    for _ in range(2):
        task.cancel()
        await asyncio.sleep(0)
    assert not task.done()
    assert manager._lock.locked()
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.2
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager.config.enabled
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8
    assert manager.active is None
    assert not manager._unsubscribers


async def test_invalid_control_readback_installs_neither_half_and_retries_safely(
    hass, monkeypatch, timers
):
    manager = await _manager(hass, monkeypatch, True)
    baseline = deepcopy(manager.active)
    persisted = {"config": _config(enabled=False).as_dict(), "active": "broken"}
    manager._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("write failed")),
        async_load=AsyncMock(side_effect=lambda: deepcopy(persisted)),
    )
    with pytest.raises(OSError, match="write failed"):
        await manager._async_save_control_state_locked()
    assert manager.config.enabled
    assert manager.active == baseline
    assert manager._config_recovery_pending
    assert len(manager._unsubscribers) == 1
    hass.services.async_call.reset_mock()
    await manager.async_reconcile()
    hass.services.async_call.assert_not_awaited()
    persisted["active"] = baseline
    manager._store.async_save.side_effect = None
    await manager.async_reconcile()
    assert not manager.config.enabled
    assert manager.active is None
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8


@pytest.mark.parametrize("shutdown", [False, True])
async def test_unreadable_generation_blocks_direct_restore_and_shutdown_actions(
    hass, monkeypatch, timers, shutdown
):
    manager = await _manager(hass, monkeypatch, True)
    manager._active["controls"]["media_player.bedroom"]["original_value"] = 0.6
    manager._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("write acknowledgement lost")),
        async_load=AsyncMock(side_effect=OSError("read unavailable")),
    )
    with pytest.raises(OSError, match="write acknowledgement"):
        await manager._async_save_control_state_locked()
    hass.services.async_call.reset_mock()
    if shutdown:
        await manager.async_shutdown()
    else:
        await manager._async_restore_locked()
    hass.services.async_call.assert_not_awaited()
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.2
    assert manager._config_recovery_pending


async def test_cancelled_recovery_rearms_timers_for_the_installed_generation(
    hass, monkeypatch, timers
):
    manager = await _manager(hass, monkeypatch, False)
    manager._config_recovery_pending = True
    manager._initialized = False
    manager._reschedule()
    assert len(manager._unsubscribers) == 1
    entered, finish = asyncio.Event(), asyncio.Event()

    async def load():
        entered.set()
        await finish.wait()
        return {"config": _config(enabled=True).as_dict(), "active": None}

    manager._store.async_load = load
    task = asyncio.create_task(manager.async_reconcile())
    await entered.wait()
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.config.enabled
    assert not manager._config_recovery_pending
    assert len(manager._unsubscribers) == 3
    await manager.async_reconcile()
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.2


@pytest.mark.parametrize("enabled", [False, True])
async def test_shutdown_cancels_callbacks_rearmed_by_successful_recovery(
    hass, monkeypatch, timers, enabled
):
    manager = await _manager(hass, monkeypatch, True)
    authoritative = {
        "config": _config(enabled=enabled).as_dict(),
        "active": deepcopy(manager.active),
    }
    manager._active["controls"]["media_player.bedroom"]["original_value"] = 0.6
    manager._store = SimpleNamespace(
        async_save=AsyncMock(side_effect=OSError("write acknowledgement lost")),
        async_load=AsyncMock(side_effect=OSError("read unavailable")),
    )
    with pytest.raises(OSError):
        await manager._async_save_control_state_locked()
    manager._store.async_load.side_effect = None
    manager._store.async_load.return_value = authoritative
    manager._store.async_save.side_effect = None
    await manager.async_shutdown()
    assert hass.states.get("media_player.bedroom").attributes["volume_level"] == 0.8
    assert manager.active is None
    assert not manager._unsubscribers
