"""Guest observers follow authoritative UTC boundaries without polling."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import guest_mode
from homeassistant.core import is_callback
from homeassistant.exceptions import HomeAssistantError


class ScheduleStore:
    def __init__(self):
        self.data = {"schedule": None}
        self.fail_save = False
        self.fail_load = False

    async def async_load(self):
        if self.fail_load:
            raise OSError("readback failed")
        return deepcopy(self.data)

    async def async_save(self, data):
        if self.fail_save:
            raise OSError("write failed")
        self.data = deepcopy(data)


class PointTimers:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def track(self, _hass, action, when):
        assert is_callback(action)  # HA must dispatch on its event loop.
        assert when.tzinfo is UTC
        timer = SimpleNamespace(action=action, when=when, cancelled=False, fired=False)
        self.calls.append(timer)

        def cancel():
            timer.cancelled = True

        return cancel

    @property
    def pending(self):
        return [
            timer for timer in self.calls if not timer.cancelled and not timer.fired
        ]

    def fire(self, timer):
        timer.fired = True
        timer.action(self.clock[0])


@pytest.fixture
def env(hass, monkeypatch):
    clock = [datetime(2026, 11, 16, 12, tzinfo=UTC)]
    timers = PointTimers(clock)
    monkeypatch.setattr(guest_mode.dt_util, "utcnow", lambda: clock[0])
    monkeypatch.setattr(guest_mode, "async_track_point_in_utc_time", timers.track)
    manager = guest_mode.GuestModeManager(hass, "entry", "agent")
    store = manager._store = ScheduleStore()

    def schedule(start, end):
        return guest_mode.GuestModeSchedule(
            (clock[0] + timedelta(minutes=start)).isoformat(),
            (clock[0] + timedelta(minutes=end)).isoformat()
            if end is not None
            else None,
        )

    def next_at(minutes):
        assert len(timers.pending) == 1
        timer = timers.pending[0]
        assert timer.when == clock[0] + timedelta(minutes=minutes)
        return timer

    return SimpleNamespace(
        clock=clock,
        timers=timers,
        manager=manager,
        store=store,
        schedule=schedule,
        next_at=next_at,
    )


async def test_finite_schedule_notifies_at_inclusive_start_and_exclusive_end(env):
    manager = env.manager
    await manager.async_initialize()
    observed = []
    remove = manager.async_add_listener(
        lambda: observed.append(manager.status()["state"])
    )
    await manager.async_replace_backup(env.schedule(10, 20))
    assert observed == ["scheduled"]
    start = env.next_at(10)
    env.clock[0] = start.when
    env.timers.fire(start)
    assert observed == ["scheduled", "active"]
    end = env.next_at(10)
    env.timers.fire(start)  # A queued duplicate cannot announce another transition.
    assert observed == ["scheduled", "active"]
    env.clock[0] = end.when
    env.timers.fire(end)
    assert observed == ["scheduled", "active", "inactive"]
    assert not env.timers.pending
    remove()


@pytest.mark.parametrize(
    "start,end,state,next_minutes",
    [
        (10, None, "scheduled", 10),
        (0, 20, "active", 20),
        (-5, 20, "active", 20),
        (-5, None, "active_indefinitely", None),
        (-20, 0, "inactive", None),
        (-20, -10, "inactive", None),
    ],
)
async def test_startup_arms_only_the_next_future_boundary(
    env, start, end, state, next_minutes
):
    schedule = env.schedule(start, end)
    env.store.data = {"schedule": asdict(schedule)}
    observed = []
    remove = env.manager.async_add_listener(
        lambda: observed.append(env.manager.status()["state"])
    )
    assert not env.timers.pending
    await env.manager.async_initialize()
    assert observed == [state]
    if next_minutes is None:
        assert not env.timers.pending
    else:
        timer = env.next_at(next_minutes)
        env.clock[0] = timer.when
        env.timers.fire(timer)
        assert observed == [state, "active_indefinitely" if end is None else "inactive"]
        assert not env.timers.pending
    await env.manager.async_initialize()
    assert len(observed) == (1 if next_minutes is None else 2)
    remove()


@pytest.mark.parametrize(
    "operation,next_minutes",
    [
        ("trusted", 30),
        ("restrict", 5),
        ("backup", 25),
        ("disable", None),
    ],
)
async def test_schedule_replacement_cancels_stale_callbacks(
    env, operation, next_minutes
):
    await env.manager.async_initialize()
    observed = []
    remove = env.manager.async_add_listener(
        lambda: observed.append(env.manager.status())
    )
    await env.manager.async_replace_backup(env.schedule(10, 20))
    old = env.next_at(10)
    if operation == "trusted":
        await env.manager.async_update_trusted(
            active_from=env.schedule(30, 40).active_from,
            active_until=env.schedule(30, 40).active_until,
        )
    elif operation == "restrict":
        await env.manager.async_restrict(
            active_from=env.schedule(5, 40).active_from,
            active_until=env.schedule(5, 40).active_until,
        )
    elif operation == "backup":
        await env.manager.async_replace_backup(env.schedule(25, 35))
    else:
        await env.manager.async_disable_trusted()
    assert old.cancelled
    assert len(observed) == 2
    env.timers.fire(old)
    assert len(observed) == 2
    if next_minutes is None:
        assert not env.timers.pending
    else:
        env.next_at(next_minutes)
    remove()


async def test_one_timer_is_shared_and_last_listener_teardown_cancels_it(env):
    await env.manager.async_initialize()
    await env.manager.async_replace_backup(env.schedule(10, 20))
    assert not env.timers.pending
    calls = []
    first = env.manager.async_add_listener(lambda: calls.append("first"))
    timer = env.next_at(10)
    second = env.manager.async_add_listener(lambda: calls.append("second"))
    assert env.timers.calls == [timer]
    first()
    assert not timer.cancelled
    env.clock[0] = timer.when
    env.timers.fire(timer)
    assert calls == ["second"]
    next_timer = env.next_at(10)
    second()
    second()  # Unsubscribe is idempotent.
    assert next_timer.cancelled
    assert not env.timers.pending
    env.clock[0] = next_timer.when
    env.timers.fire(next_timer)
    assert calls == ["second"]
    await env.manager.async_replace_backup(env.schedule(10, 20))
    assert not env.timers.pending


@pytest.mark.parametrize("late", [False, True])
async def test_timer_rechecks_the_current_clock_without_speculative_transitions(
    env, late
):
    await env.manager.async_initialize()
    observed = []
    remove = env.manager.async_add_listener(
        lambda: observed.append(env.manager.status()["state"])
    )
    await env.manager.async_replace_backup(env.schedule(10, 20))
    timer = env.next_at(10)
    env.clock[0] += timedelta(minutes=30 if late else 9)
    env.timers.fire(timer)
    if late:
        assert observed == ["scheduled", "inactive"]
        assert not env.timers.pending
    else:
        assert observed == ["scheduled"]
        env.next_at(1)
    remove()


async def test_failed_write_rearms_from_readback_not_the_proposed_schedule(env):
    await env.manager.async_initialize()
    observed = []
    remove = env.manager.async_add_listener(
        lambda: observed.append(env.manager.status()["state"])
    )
    await env.manager.async_replace_backup(env.schedule(10, 20))
    old = env.next_at(10)
    durable = env.schedule(30, 40)
    env.store.fail_save = True
    env.store.data = {"schedule": asdict(durable)}
    with pytest.raises(OSError, match="write failed"):
        await env.manager.async_replace_backup(env.schedule(5, 15))
    assert old.cancelled
    assert env.manager.schedule == durable
    env.next_at(30)
    assert observed == ["scheduled", "scheduled"]
    env.timers.fire(old)
    assert len(observed) == 2
    remove()


async def test_failed_write_with_unchanged_readback_keeps_one_existing_timer(env):
    await env.manager.async_initialize()
    remove = env.manager.async_add_listener(lambda: None)
    await env.manager.async_replace_backup(env.schedule(10, 20))
    timer = env.next_at(10)
    env.store.fail_save = True
    with pytest.raises(OSError):
        await env.manager.async_disable_trusted()
    assert env.timers.pending == [timer]
    remove()


async def test_unavailability_cancels_timer_and_recovery_arms_current_boundary(env):
    await env.manager.async_initialize()
    observed = []

    def changed():
        observed.append(
            env.manager.status()["state"]
            if env.manager.authoritative_available
            else "unavailable"
        )

    remove = env.manager.async_add_listener(changed)
    await env.manager.async_replace_backup(env.schedule(10, 20))
    old = env.next_at(10)
    env.store.fail_save = env.store.fail_load = True
    with pytest.raises(OSError):
        await env.manager.async_disable_trusted()
    assert old.cancelled
    assert not env.timers.pending
    assert observed == ["scheduled", "unavailable"]
    with pytest.raises(HomeAssistantError, match="persistence is unavailable"):
        env.manager.is_active()
    env.clock[0] += timedelta(minutes=11)
    env.timers.fire(old)
    assert observed == ["scheduled", "unavailable"]
    env.store.fail_load = False
    await env.manager.async_initialize()
    assert observed == ["scheduled", "unavailable", "active"]
    env.next_at(9)
    remove()
    assert not env.timers.pending


async def test_offset_timestamps_are_scheduled_as_utc(env):
    env.store.data = {
        "schedule": {
            "active_from": "2026-11-16T14:10:00+02:00",
            "active_until": "2026-11-16T14:20:00+02:00",
        }
    }
    await env.manager.async_initialize()
    remove = env.manager.async_add_listener(lambda: None)
    env.next_at(10)
    remove()


async def test_corrupt_startup_remains_unavailable_until_valid_authoritative_read(env):
    from custom_components.extended_openai_conversation_responses.persistence_hardening import (
        StorageRepairRequired,
    )

    observed = []
    remove = env.manager.async_add_listener(
        lambda: observed.append(env.manager.status()["state"])
    )
    env.store.data = {"schedule": {"active_from": "invalid"}}
    with pytest.raises(StorageRepairRequired):
        await env.manager.async_initialize()
    assert not env.manager.authoritative_available
    assert not env.timers.pending
    assert not observed
    env.store.data = {"schedule": asdict(env.schedule(10, 20))}
    await env.manager.async_initialize()
    assert observed == ["scheduled"]
    env.next_at(10)
    remove()


async def test_boundary_observer_failure_does_not_starve_other_listeners_or_expiry(env):
    await env.manager.async_initialize()
    await env.manager.async_replace_backup(env.schedule(10, 20))
    observed = []

    def broken():
        raise RuntimeError("observer failed")

    remove_broken = env.manager.async_add_listener(broken)
    remove_good = env.manager.async_add_listener(
        lambda: observed.append(env.manager.status()["state"])
    )
    for _ in range(2):
        timer = env.timers.pending[0]
        env.clock[0] = timer.when
        env.timers.fire(timer)
    assert observed == ["active", "inactive"]
    assert not env.timers.pending
    remove_broken()
    remove_good()
