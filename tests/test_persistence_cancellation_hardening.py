"""Cancellation regressions for shared durable-manager persistence guards."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from typing import Any

import pytest

from homeassistant.util import dt as dt_util

from custom_components.extended_openai_conversation_responses.knowledge import (
    KnowledgeLibrary,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    _COMMITTED_STATE,
    _snapshot_knowledge,
    _snapshot_memory,
    _snapshot_request_rules,
    _snapshot_temporary_memory,
    install_persistence_transactions,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemory,
)

_MANAGER_TYPES = ("memory", "temporary_memory", "knowledge", "request_rules")


class BlockingStorage:
    """Storage double whose next save can be paused and failed deterministically."""

    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.block_saves = False
        self.fail_saves = False
        self.save_started = asyncio.Event()
        self.release_save = asyncio.Event()

    async def async_load(self) -> dict[str, Any] | None:
        return deepcopy(self.data)

    async def async_save(self, data: dict[str, Any]) -> None:
        candidate = deepcopy(data)
        if self.block_saves:
            self.save_started.set()
            await self.release_save.wait()
        if self.fail_saves:
            raise RuntimeError("simulated Store failure")
        self.data = candidate

    def arm(self, *, fail: bool = False) -> None:
        """Pause subsequent saves until explicitly released."""
        self.block_saves = True
        self.fail_saves = fail
        self.save_started = asyncio.Event()
        self.release_save = asyncio.Event()

    def fail_immediately(self) -> None:
        """Make subsequent saves fail without blocking."""
        self.block_saves = False
        self.fail_saves = True


async def _create_manager(kind: str, storage: BlockingStorage) -> Any:
    if kind == "memory":
        manager = PersistentMemory(storage)  # type: ignore[arg-type]
    elif kind == "temporary_memory":
        manager = TemporaryMemory(storage)  # type: ignore[arg-type]
    elif kind == "knowledge":
        manager = KnowledgeLibrary(storage)  # type: ignore[arg-type]
    elif kind == "request_rules":
        manager = RequestRules(storage)  # type: ignore[arg-type]
    else:  # pragma: no cover - protected by parametrization
        raise AssertionError(kind)
    await manager.async_initialize()
    return manager


def _snapshot(kind: str, manager: Any) -> Any:
    if kind == "memory":
        value = _snapshot_memory(manager)
    elif kind == "temporary_memory":
        value = _snapshot_temporary_memory(manager)
    elif kind == "knowledge":
        value = _snapshot_knowledge(manager)
    elif kind == "request_rules":
        value = _snapshot_request_rules(manager)
    else:  # pragma: no cover - protected by parametrization
        raise AssertionError(kind)
    return deepcopy(value)


async def _mutate(kind: str, manager: Any, marker: str) -> None:
    if kind == "memory":
        await manager.async_add(
            "user-1",
            f"Persistent memory {marker}",
            "test",
            "explicit",
        )
        return
    if kind == "temporary_memory":
        await manager.async_add(
            "user-1",
            f"Temporary memory {marker}",
            (dt_util.utcnow() + timedelta(days=30)).isoformat(),
            "test",
        )
        return
    if kind == "knowledge":
        await manager.async_create(
            f"Knowledge {marker}",
            "Reference",
            f"Knowledge body {marker}",
        )
        return
    if kind == "request_rules":
        if marker == "first":
            settings = {
                "word_forms": False,
                "wording_alternatives": True,
                "fuzzy": True,
                "fuzzy_threshold": 85,
            }
        else:
            settings = {
                "word_forms": True,
                "wording_alternatives": False,
                "fuzzy": True,
                "fuzzy_threshold": 80,
            }
        await manager.async_set_defaults(settings)
        return
    raise AssertionError(kind)  # pragma: no cover


@pytest.mark.parametrize("kind", _MANAGER_TYPES)
async def test_cancellation_waits_for_successful_commit_and_advances_snapshot(
    kind: str,
) -> None:
    """Caller cancellation is deferred until each manager's save commits."""
    install_persistence_transactions()
    storage = BlockingStorage()
    manager = await _create_manager(kind, storage)
    baseline = _snapshot(kind, manager)
    baseline_storage = deepcopy(storage.data)

    storage.arm()
    mutation = asyncio.create_task(_mutate(kind, manager, "first"))
    await asyncio.wait_for(storage.save_started.wait(), timeout=1)

    mutation.cancel()
    await asyncio.sleep(0)
    assert not mutation.done()

    # A second cancellation request must still not tear down the in-flight save.
    mutation.cancel()
    await asyncio.sleep(0)
    assert not mutation.done()

    storage.release_save.set()
    with pytest.raises(asyncio.CancelledError):
        await mutation

    committed = _snapshot(kind, manager)
    assert committed != baseline
    assert storage.data != baseline_storage
    assert deepcopy(getattr(manager, _COMMITTED_STATE)) == committed

    # Prove the cancellation-success snapshot became the rollback baseline rather
    # than merely leaving the newer live state in RAM accidentally.
    storage.fail_immediately()
    with pytest.raises(RuntimeError, match="simulated Store failure"):
        await _mutate(kind, manager, "second")
    assert _snapshot(kind, manager) == committed


@pytest.mark.parametrize("kind", _MANAGER_TYPES)
async def test_cancellation_waits_for_failed_commit_then_rolls_back(kind: str) -> None:
    """A cancelled caller sees cancellation only after failed persistence rolls back."""
    install_persistence_transactions()
    storage = BlockingStorage()
    manager = await _create_manager(kind, storage)
    baseline = _snapshot(kind, manager)
    baseline_storage = deepcopy(storage.data)

    storage.arm(fail=True)
    mutation = asyncio.create_task(_mutate(kind, manager, "first"))
    await asyncio.wait_for(storage.save_started.wait(), timeout=1)

    mutation.cancel()
    await asyncio.sleep(0)
    assert not mutation.done()

    storage.release_save.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await mutation

    assert isinstance(cancelled.value.__cause__, RuntimeError)
    assert _snapshot(kind, manager) == baseline
    assert storage.data == baseline_storage
    assert deepcopy(getattr(manager, _COMMITTED_STATE)) == baseline
