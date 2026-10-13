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


async def test_cancelled_readback_retains_lock_until_authoritative_installation():
    entered, finish = asyncio.Event(), asyncio.Event()
    lock = asyncio.Lock()
    state = {"generation": "candidate", "available": True}

    async def save():
        raise OSError("commit acknowledgement lost")

    async def readback():
        entered.set()
        await finish.wait()
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

    task = asyncio.create_task(update())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert lock.locked()
    assert state["generation"] == "candidate"
    finish.set()
    with pytest.raises(asyncio.CancelledError) as raised:
        await task
    assert isinstance(raised.value.__cause__, OSError)
    assert state == {"generation": "durable", "available": True}
    assert not lock.locked()


async def test_internal_save_neither_starts_nor_settles_enclosing_effect():
    entity = SimpleNamespace()
    call = _call("outer", "effect", {})
    tool = {"spec": {"name": "effect", "parameters": {"type": "object"}}}
    with execution_attempt(entity, call, tool):
        execution_started()
        await _async_settle_transactional_save(
            AsyncMock()(),
            lambda: None,
            lambda: None,
            None,
            lambda: None,
            record_execution=False,
        )
        execution_completed()
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
