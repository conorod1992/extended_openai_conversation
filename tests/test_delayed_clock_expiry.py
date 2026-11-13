"""Wall-clock correctness and transactional expiry for durable Function Tools."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import delayed_tools
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    validate_function_tools,
)
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
from tests.test_delayed_tools import _entity, _record, _valid_tool


class Store:
    def __init__(self, calls=()):
        self.payload = {"calls": [call.as_dict() for call in calls]}
        self.fail = None
        self.writes = []

    async def async_load(self):
        return deepcopy(self.payload)

    async def async_save(self, payload):
        self.writes.append(deepcopy(payload))
        if self.fail == "before":
            raise OSError("write failed")
        self.payload = deepcopy(payload)
        if self.fail == "after":
            raise OSError("acknowledgement failed")


def _manager(hass, monkeypatch, record):
    manager = delayed_tools.DelayedToolManager(hass)
    manager._setup_complete = True
    manager._records = {record.call_id: record}
    manager._store = Store([record])
    tool = _valid_tool()
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        disabled_by=None,
        subentries={"agent": SimpleNamespace(subentry_type="conversation", data={})},
    )
    hass.auth.async_get_user = AsyncMock(return_value=SimpleNamespace(is_active=True))
    monkeypatch.setattr(
        delayed_tools, "configured_function_tools_from_data", lambda _: [tool]
    )
    monkeypatch.setattr(delayed_tools, "get_exposed_entities", lambda _: [])
    execute = AsyncMock()
    monkeypatch.setattr(
        manager,
        "_resolve_agent",
        lambda *_: SimpleNamespace(_execute_function_tool=execute),
    )
    return manager, execute


@pytest.mark.parametrize("catch_up", [False, True])
async def test_new_policy_round_trips_and_does_not_change_fingerprint(
    hass, monkeypatch, catch_up
):
    now = dt_util.utcnow()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    tool = _valid_tool()
    fingerprint = delayed_tools.tool_definition_fingerprint(tool)
    tool["delay_catch_up"] = catch_up
    assert delayed_tools.tool_definition_fingerprint(tool) == fingerprint
    first = delayed_tools.DelayedToolManager(hass)
    first._setup_complete = True
    first._store = Store()
    record = await first.async_schedule(
        _entity(hass),
        "control_light",
        {},
        None,
        function_tool=tool,
        execution_delay={"seconds": 5},
    )
    assert record.expires_at == (
        None if catch_up else (now + timedelta(seconds=905)).isoformat()
    )
    assert first._store.payload["calls"][0]["expires_at"] == record.expires_at
    # Recovery retains the selected semantics rather than applying today's default.
    second = delayed_tools.DelayedToolManager(hass)
    second._store = first._store
    hass.state = CoreState.not_running
    await second.async_setup()
    assert second._records[record.call_id] == record
    now += timedelta(hours=1)
    due_manager, execute = _manager(hass, monkeypatch, second._records[record.call_id])
    assert await due_manager._async_execute_due(record.call_id) is False
    assert execute.await_count == int(catch_up)


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize(
    "offset,executes", [(-0.001, True), (0, False), (0.001, False)]
)
async def test_expiry_boundary_fresh_and_recovered(
    hass, monkeypatch, recovered, offset, executes
):
    now = dt_util.utcnow()
    expiry = now - timedelta(seconds=offset)
    record = replace(
        _record(),
        due_at=(now - timedelta(seconds=900)).isoformat(),
        expires_at=expiry.isoformat(),
    )
    manager, execute = _manager(hass, monkeypatch, record)
    if recovered:
        manager._records.clear()
        manager._setup_complete = False
        hass.state = CoreState.not_running
        await manager.async_setup()
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    assert await manager._async_execute_due(record.call_id) is False
    assert execute.await_count == int(executes)
    assert manager._store.payload == {"calls": []}
    statuses = [
        raw["status"] for write in manager._store.writes for raw in write["calls"]
    ]
    assert ("executing" in statuses) is executes


async def test_legacy_jobs_keep_eventual_execution(hass, monkeypatch):
    record = replace(
        _record(), due_at=(dt_util.utcnow() - timedelta(days=7)).isoformat()
    )
    legacy = record.as_dict()
    legacy.pop("expires_at")
    record = delayed_tools.DelayedToolCall.from_dict(legacy)
    manager, execute = _manager(hass, monkeypatch, record)
    assert await manager._async_execute_due(record.call_id) is False
    execute.assert_awaited_once()


@pytest.mark.parametrize("failure", ["before", "after"])
async def test_failed_expiry_removal_never_executes_and_reconciles(
    hass, monkeypatch, failure
):
    record = replace(_record(), expires_at=dt_util.utcnow().isoformat())
    manager, execute = _manager(hass, monkeypatch, record)
    manager._store.fail = failure
    assert await manager._async_execute_due(record.call_id) is (failure == "before")
    execute.assert_not_awaited()
    assert (record.call_id in manager._records) is (failure == "before")
    manager._store.fail = None
    assert await manager._async_execute_due(record.call_id) is False
    assert manager._store.payload == {"calls": []}
    execute.assert_not_awaited()


@pytest.mark.parametrize("phase", ["gate", "auth", "storage"])
@pytest.mark.parametrize("jump", ["backward", "expire"])
async def test_time_rechecked_after_every_authorization_wait(
    hass, monkeypatch, phase, jump
):
    now = dt_util.utcnow()
    clock = [now]
    record = replace(
        _record(),
        due_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=15)).isoformat(),
    )
    manager, execute = _manager(hass, monkeypatch, record)
    monkeypatch.setattr(dt_util, "utcnow", lambda: clock[0])

    def adjust():
        clock[0] = now + timedelta(seconds=-60 if jump == "backward" else 900)

    if phase == "gate":

        class Gate:
            @asynccontextmanager
            async def shared(self):
                adjust()
                yield

        monkeypatch.setattr(
            delayed_tools, "get_agent_maintenance_gate", lambda *_: Gate()
        )
    elif phase == "auth":

        async def user(_):
            adjust()
            return SimpleNamespace(is_active=True)

        hass.auth.async_get_user.side_effect = user
    else:
        await manager._lock.acquire()
        task = asyncio.create_task(manager._async_execute_due(record.call_id))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        adjust()
        manager._lock.release()
        assert await task is (jump == "backward")
        execute.assert_not_awaited()
        return
    assert await manager._async_execute_due(record.call_id) is (jump == "backward")
    execute.assert_not_awaited()
    if jump == "expire":
        assert manager._store.payload == {"calls": []}
    else:
        assert manager._store.payload["calls"][0]["status"] == "pending"


async def test_waiter_bounds_sleeps_and_resamples_both_clock_jumps(hass, monkeypatch):
    now = dt_util.utcnow()
    clock = [now]
    record = replace(_record(), due_at=(now + timedelta(hours=1)).isoformat())
    manager, execute = _manager(hass, monkeypatch, record)
    manager._started = True
    monkeypatch.setattr(dt_util, "utcnow", lambda: clock[0])
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            clock[0] -= timedelta(hours=1)
        elif len(sleeps) == 2:
            clock[0] += timedelta(hours=2)

    monkeypatch.setattr(delayed_tools.asyncio, "sleep", sleep)
    await manager._async_wait_and_execute(record.call_id)
    assert sleeps == [30, 30, 0]
    execute.assert_awaited_once()


async def test_expiry_after_agent_retry_and_failed_claim(hass, monkeypatch):
    now = dt_util.utcnow()
    clock = [now]
    record = replace(_record(), expires_at=(now + timedelta(seconds=1)).isoformat())
    manager, execute = _manager(hass, monkeypatch, record)
    monkeypatch.setattr(dt_util, "utcnow", lambda: clock[0])
    original_resolve = manager._resolve_agent
    monkeypatch.setattr(manager, "_resolve_agent", lambda *_: None)
    assert await manager._async_execute_due(record.call_id) is True
    assert manager._records[record.call_id].retry_count == 1
    monkeypatch.setattr(manager, "_resolve_agent", original_resolve)
    manager._store.fail = "before"
    assert await manager._async_execute_due(record.call_id) is True
    clock[0] += timedelta(seconds=1)
    assert await manager._async_execute_due(record.call_id) is True
    execute.assert_not_awaited()
    manager._store.fail = None
    assert await manager._async_execute_due(record.call_id) is False
    execute.assert_not_awaited()


async def test_cancelled_waiter_and_deleted_call_cannot_claim_execution(
    hass, monkeypatch
):
    record = _record()
    manager, execute = _manager(hass, monkeypatch, record)
    await manager._lock.acquire()
    task = asyncio.create_task(manager._async_execute_due(record.call_id))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Simulate deletion winning the serialized storage generation.
    manager._records.clear()
    manager._store.payload = {"calls": []}
    manager._lock.release()
    assert await task is False
    execute.assert_not_awaited()
    assert not manager._store.writes


@pytest.mark.parametrize("expiry", [False, 4, "invalid", "2000-01-01T00:00:00+00:00"])
def test_invalid_expiry_metadata_is_rejected(expiry):
    raw = _record().as_dict()
    raw["expires_at"] = expiry
    with pytest.raises(ValueError, match="expires_at"):
        delayed_tools.DelayedToolCall.from_dict(raw)


def test_catch_up_configuration_requires_boolean():
    tool = {
        "spec": {"name": "wait"},
        "function": {"type": "template", "value_template": "ready"},
        "delay_catch_up": "true",
    }
    with pytest.raises(AgentConfigError, match="delay_catch_up"):
        validate_function_tools([tool])
    tool["delay_catch_up"] = True
    assert validate_function_tools([tool])[0]["delay_catch_up"] is True


async def test_cancel_during_expiry_write_settles_removal_without_execution(
    hass, monkeypatch
):
    record = replace(_record(), expires_at=dt_util.utcnow().isoformat())
    manager, execute = _manager(hass, monkeypatch, record)
    entered, release = asyncio.Event(), asyncio.Event()
    save = manager._store.async_save

    async def blocking_save(payload):
        entered.set()
        await release.wait()
        await save(payload)

    manager._store.async_save = blocking_save
    task = asyncio.create_task(manager._async_execute_due(record.call_id))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager._store.payload == {"calls": []}
    assert manager._records == {}
    execute.assert_not_awaited()


@pytest.mark.parametrize("metadata", ["absent", "catch_up", "finite"])
def test_loaded_expiry_metadata_retains_its_wire_shape_across_updates(metadata):
    raw = _record().as_dict()
    if metadata == "absent":
        raw.pop("expires_at")
    elif metadata == "finite":
        raw["expires_at"] = (dt_util.utcnow() + timedelta(minutes=15)).isoformat()
    record = delayed_tools.DelayedToolCall.from_dict(raw)
    assert record.as_dict() == raw
    assert replace(record, retry_count=1).as_dict() == {**raw, "retry_count": 1}
    assert "_expiry_metadata_present" not in record.as_dict()
