"""Short exclusive boundary for coherent agent backup snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Protocol


class BackupSnapshotParticipant(Protocol):
    """One mutable manager that can contribute a lock-protected local snapshot."""

    @property
    def backup_snapshot_lock(self) -> asyncio.Lock:
        """Return the manager lock that guards its mutable durable state."""

    def backup_snapshot_locked(self) -> Any:
        """Copy JSON-compatible local state while backup_snapshot_lock is held."""


@dataclass(slots=True)
class BackupSnapshotAdapter:
    """Expose an existing manager lock and synchronous copier to the boundary."""

    backup_snapshot_lock: asyncio.Lock
    snapshot_locked: Callable[[], Any]

    def backup_snapshot_locked(self) -> Any:
        """Copy the manager state while its existing mutation lock is held."""
        return self.snapshot_locked()


async def async_collect_point_in_time_snapshot(
    participants: Sequence[BackupSnapshotParticipant],
) -> tuple[Any, ...]:
    """Freeze all participants briefly and copy one coherent local snapshot.

    Locks are acquired in caller-provided order and released in reverse order. The
    participant snapshot methods must be synchronous and must not perform storage I/O;
    slower validation, redaction, serialization, and file handling belong after this
    boundary has been released.
    """
    locks = [participant.backup_snapshot_lock for participant in participants]
    if len({id(lock) for lock in locks}) != len(locks):
        raise ValueError("backup snapshot participants must use distinct locks")

    async with AsyncExitStack() as stack:
        for lock in locks:
            await stack.enter_async_context(lock)
        return tuple(participant.backup_snapshot_locked() for participant in participants)
