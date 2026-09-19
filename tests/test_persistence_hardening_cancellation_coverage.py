"""Direct owner coverage for cancellation of the underlying Store task."""

import asyncio

import pytest

from tests.test_persistence_cancellation_hardening import (
    BlockingStorage,
    _create_manager,
    _mutate,
    _snapshot,
)


@pytest.mark.parametrize("kind", ["memory", "knowledge", "request_rules"])
async def test_cancelled_store_restores_committed_state(kind):
    storage = BlockingStorage()
    manager = await _create_manager(kind, storage)
    await _mutate(kind, manager, "first")
    committed = _snapshot(kind, manager)

    async def cancelled_save(data):
        raise asyncio.CancelledError

    storage.async_save = cancelled_save
    with pytest.raises(asyncio.CancelledError):
        await _mutate(kind, manager, "second")
    assert _snapshot(kind, manager) == committed
