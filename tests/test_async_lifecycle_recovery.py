"""Regression tests for cancellation and background-task recovery."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses import intercom
from custom_components.extended_openai_conversation_responses import usage as usage_module
from custom_components.extended_openai_conversation_responses.functions import bash
from custom_components.extended_openai_conversation_responses.functions.bash import (
    BashFunction,
)
from custom_components.extended_openai_conversation_responses.intercom import (
    BroadcastMessage,
    Delivery,
    IntercomManager,
)
from custom_components.extended_openai_conversation_responses.usage import (
    UsageManager,
    UsageRequest,
)


class _FakeProcess:
    """Minimal subprocess whose lifetime is controlled by terminate/kill."""

    def __init__(self) -> None:
        self.pid = 12345
        self.returncode = None
        self._done = asyncio.Event()
        self.terminate_calls = 0
        self.kill_calls = 0
        self.stdout = _FakeStream(self)
        self.stderr = _FakeStream(self)

    async def wait(self) -> int:
        await self._done.wait()
        return int(self.returncode)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = -15
        self._done.set()

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self._done.set()


class _FakeStream:
    def __init__(self, process: _FakeProcess) -> None:
        self._process = process
        self._finished = False

    async def read(self, _size: int) -> bytes:
        if self._finished:
            return b""
        await self._process._done.wait()
        self._finished = True
        return b""


async def test_bash_cancellation_terminates_child_and_reraises(
    hass, monkeypatch
) -> None:
    """Cancelling Bash must settle the child instead of orphaning it."""
    function = BashFunction()
    process = _FakeProcess()
    monkeypatch.setattr(
        bash.asyncio,
        "create_subprocess_shell",
        AsyncMock(return_value=process),
    )
    if bash.os.name == "posix":

        def kill_group(_pid: int, sig) -> None:
            if sig == bash.signal.SIGTERM:
                process.terminate()
                return
            if process.returncode is not None:
                raise ProcessLookupError
            process.kill()

        monkeypatch.setattr(bash.os, "killpg", kill_group)
    monkeypatch.setattr(function, "_async_guard_command", AsyncMock())
    function_config = {
        "allow_unsafe_shell": True,
        "command": Template("long-running-command", hass),
    }

    task = asyncio.create_task(
        function.execute(hass, function_config, {}, None, [])
    )
    for _ in range(100):
        if bash.asyncio.create_subprocess_shell.await_count:
            break
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert process.returncode == -15


async def test_bash_cleanup_signals_group_after_shell_leader_exits(
    monkeypatch,
) -> None:
    """POSIX cleanup owns the process group even after its leader has exited."""
    if bash.os.name != "posix":
        pytest.skip("POSIX process-group behavior")

    process = _FakeProcess()
    process.returncode = 0
    process._done.set()
    stdout_task = asyncio.create_task(bash._read_bounded_stream(process.stdout))
    stderr_task = asyncio.create_task(bash._read_bounded_stream(process.stderr))
    signals = []

    def kill_group(pid: int, sig) -> None:
        signals.append((pid, sig))

    monkeypatch.setattr(bash.os, "killpg", kill_group)

    await bash._async_cleanup_process(
        process, stdout_task, stderr_task, graceful=False
    )

    assert (process.pid, bash.signal.SIGKILL) in signals


async def test_broadcast_idle_transition_during_active_drain_is_rescheduled(
    hass, monkeypatch
) -> None:
    """An idle callback lost to `_draining` is recovered during finalization."""
    manager = IntercomManager(hass)
    manager._enabled = True
    entity_id = "assist_satellite.kitchen"
    item = BroadcastMessage(
        id="race-message",
        message="Dinner is ready",
        created_at=intercom.datetime.now(intercom.UTC).isoformat(),
        expires_at=intercom.datetime.now(intercom.UTC) + intercom.timedelta(seconds=30),
        source="test",
        origin_entity_id=None,
        origin_device_id=None,
        targets=[entity_id],
        deliveries={entity_id: Delivery(entity_id, "queued_busy")},
    )
    manager._queues[entity_id] = deque([item])
    manager._draining.add(entity_id)

    scheduled: list[str] = []

    def schedule_drain(target: str) -> None:
        if target in manager._draining:
            return
        manager._draining.add(target)
        scheduled.append(target)

    monkeypatch.setattr(manager, "_schedule_drain", schedule_drain)
    state_reads = 0

    def get_state(_entity_id: str):
        nonlocal state_reads
        state_reads += 1
        if state_reads == 1:
            manager._async_state_changed(
                SimpleNamespace(
                    data={
                        "entity_id": entity_id,
                        "new_state": SimpleNamespace(state="idle"),
                    }
                )
            )
            return SimpleNamespace(state="responding")
        return SimpleNamespace(state="idle")

    monkeypatch.setattr(hass.states, "get", get_state)

    await manager._async_drain(entity_id)

    assert item.deliveries[entity_id].status == "queued_busy"
    assert scheduled == [entity_id]
    assert entity_id in manager._draining


async def test_failed_usage_prune_retries_after_cooldown_same_day(monkeypatch) -> None:
    """A transient detail-store failure is retried once after the same-day cooldown."""

    class Storage:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.saves = 0
            self.data = None

        async def async_load(self):
            return None

        async def async_save(self, data) -> None:
            self.saves += 1
            if self.fail:
                raise OSError("temporary store failure")
            self.data = data

    old = (dt_util.utcnow() - timedelta(days=120)).isoformat()
    detail = Storage(fail=True)
    manager = UsageManager(
        Storage(),
        detail_storage=detail,
        agent_subentry_id="agent",
        request_retention_days=30,
        run_retention_days=90,
    )
    manager.requests = [
        UsageRequest(
            request_id="old-request",
            run_id="old-run",
            timestamp=old,
            agent_subentry_id="agent",
            provider="openai",
            model="gpt-5.6",
            api_mode="responses",
            successful=True,
            duration_ms=1,
        )
    ]

    monotonic = [100.0]
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: monotonic[0])

    await manager._async_prune_usage_if_due()
    first = manager._prune_task
    assert first is not None
    await first
    await asyncio.sleep(0)

    today = dt_util.utcnow().date().isoformat()
    assert [item.request_id for item in manager.requests] == ["old-request"]
    assert manager._last_prune_date != today
    assert manager._next_prune_retry == 400.0
    assert manager._prune_attempt_count == 1
    assert detail.saves == 1

    await manager._async_prune_usage_if_due()
    assert manager._prune_task is None
    assert detail.saves == 1

    detail.fail = False
    monotonic[0] = 401.0
    await manager._async_prune_usage_if_due()
    second = manager._prune_task
    assert second is not None
    await second
    await asyncio.sleep(0)

    assert detail.saves == 2
    assert manager.requests == []
    assert manager._last_prune_date == today
    assert manager._next_prune_retry == 0.0
    assert manager._prune_attempt_count == 2

