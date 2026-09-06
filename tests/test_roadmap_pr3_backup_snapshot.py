"""Focused regressions for roadmap PR3 point-in-time backup collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.backup_snapshot import (
    BackupSnapshotAdapter,
    async_collect_point_in_time_snapshot,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestModeManager,
)


@dataclass
class _Participant:
    name: str
    all_participants: list["_Participant"]

    def __post_init__(self) -> None:
        self.backup_snapshot_lock = asyncio.Lock()
        self.value = 0
        self.saw_all_locked = False

    def backup_snapshot_locked(self) -> dict[str, int]:
        self.saw_all_locked = all(
            participant.backup_snapshot_lock.locked()
            for participant in self.all_participants
        )
        return {self.name: self.value}


async def test_snapshot_adapter_reuses_existing_manager_lock_and_copier() -> None:
    lock = asyncio.Lock()
    state = {"value": 7}
    saw_locked = False

    def copy_state() -> dict[str, int]:
        nonlocal saw_locked
        saw_locked = lock.locked()
        return dict(state)

    participant = BackupSnapshotAdapter(lock, copy_state)

    snapshots = await async_collect_point_in_time_snapshot((participant,))

    assert snapshots == ({"value": 7},)
    assert saw_locked
    assert not lock.locked()


async def test_snapshot_boundary_holds_all_manager_locks_while_copying() -> None:
    participants: list[_Participant] = []
    participants.extend(
        _Participant(name, participants) for name in ("memory", "archive", "usage")
    )
    for index, participant in enumerate(participants, start=1):
        participant.value = index

    snapshots = await async_collect_point_in_time_snapshot(participants)

    assert snapshots == ({"memory": 1}, {"archive": 2}, {"usage": 3})
    assert all(participant.saw_all_locked for participant in participants)
    assert all(
        not participant.backup_snapshot_lock.locked() for participant in participants
    )


async def test_snapshot_boundary_blocks_mutation_until_all_copies_are_taken() -> None:
    participants: list[_Participant] = []
    participants.extend(_Participant(name, participants) for name in ("one", "two"))
    first, second = participants
    first.value = 10
    second.value = 20

    mutation_started = asyncio.Event()
    mutation_finished = asyncio.Event()

    async def mutate_first() -> None:
        mutation_started.set()
        async with first.backup_snapshot_lock:
            first.value = 99
        mutation_finished.set()

    # Make the collector acquire the first participant and wait on the second. A
    # concurrent writer then has to wait behind the collector's already-held lock.
    await second.backup_snapshot_lock.acquire()
    collector = asyncio.create_task(async_collect_point_in_time_snapshot(participants))
    await asyncio.sleep(0)
    assert first.backup_snapshot_lock.locked()

    mutator = asyncio.create_task(mutate_first())
    await mutation_started.wait()
    assert not mutation_finished.is_set()

    second.backup_snapshot_lock.release()
    snapshots = await collector
    await mutator

    assert snapshots == ({"one": 10}, {"two": 20})
    assert first.value == 99


async def test_snapshot_boundary_never_returns_mixed_time_versions() -> None:
    first_lock = asyncio.Lock()
    second_lock = asyncio.Lock()
    state = {"first": 1, "second": 1}

    participants = (
        BackupSnapshotAdapter(first_lock, lambda: {"version": state["first"]}),
        BackupSnapshotAdapter(second_lock, lambda: {"version": state["second"]}),
    )

    # Hold the second lock so the collector acquires the first and waits. A writer
    # that updates both categories then cannot slip between the two snapshot copies.
    await second_lock.acquire()
    collector = asyncio.create_task(async_collect_point_in_time_snapshot(participants))
    await asyncio.sleep(0)
    assert first_lock.locked()

    writer_started = asyncio.Event()

    async def write_new_version() -> None:
        writer_started.set()
        async with first_lock:
            state["first"] = 2
        async with second_lock:
            state["second"] = 2

    writer = asyncio.create_task(write_new_version())
    await writer_started.wait()
    second_lock.release()

    snapshots = await collector
    await writer

    assert snapshots == ({"version": 1}, {"version": 1})
    assert state == {"first": 2, "second": 2}


async def test_snapshot_boundary_releases_all_locks_when_copy_fails() -> None:
    first_lock = asyncio.Lock()
    second_lock = asyncio.Lock()

    def copy_first() -> dict[str, int]:
        return {"one": 1}

    def copy_second() -> dict[str, int]:
        raise RuntimeError("copy failed")

    participants = (
        BackupSnapshotAdapter(first_lock, copy_first),
        BackupSnapshotAdapter(second_lock, copy_second),
    )

    with pytest.raises(RuntimeError, match="copy failed"):
        await async_collect_point_in_time_snapshot(participants)

    assert not first_lock.locked()
    assert not second_lock.locked()


async def test_snapshot_boundary_rejects_duplicate_manager_locks() -> None:
    lock = asyncio.Lock()
    participants = (
        BackupSnapshotAdapter(lock, lambda: {"one": 1}),
        BackupSnapshotAdapter(lock, lambda: {"two": 2}),
    )

    with pytest.raises(ValueError, match="distinct locks"):
        await async_collect_point_in_time_snapshot(participants)

    assert not lock.locked()


async def test_guest_mode_mutation_waits_for_snapshot_lock(hass) -> None:
    manager = GuestModeManager(hass, "entry", "agent")
    manager._initialized = True
    manager._store.async_save = AsyncMock()

    await manager._lock.acquire()
    update = asyncio.create_task(
        manager.async_update_trusted(
            active_from="2026-09-06T10:00:00+00:00",
            indefinite=True,
        )
    )
    await asyncio.sleep(0)

    assert manager.schedule is None
    manager._lock.release()
    await update

    assert manager.schedule is not None
    assert manager.schedule.active_from == "2026-09-06T10:00:00+00:00"
    manager._store.async_save.assert_awaited_once()
