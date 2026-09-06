"""Focused regressions for roadmap PR3 point-in-time backup collection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from custom_components.extended_openai_conversation_responses.backup_snapshot import (
    async_collect_point_in_time_snapshot,
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
