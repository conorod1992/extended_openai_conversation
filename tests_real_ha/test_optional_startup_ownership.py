"""Optional initializer cleanup retains the real agent's maintenance lease."""

import asyncio

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as product,
)
from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    conversation_request_lease,
    get_agent_maintenance_gate,
)
from tests_real_ha.test_cross_feature_acceptance import _agent


@pytest.mark.parametrize("child_cancellation", [False, True])
async def test_optional_cleanup_blocks_maintenance_then_allows_retry(
    hass, monkeypatch, child_cancellation
):
    agent = await _agent(hass, title="Optional startup owner", memory_mode="manual")
    memory = agent._memory
    gate = get_agent_maintenance_gate(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished, maintenance_entered = asyncio.Event(), asyncio.Event()
    reads = []
    task = maintenance = waiter = None

    async def held_memory(*_):
        reads.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            finished.set()

    original_knowledge = product.async_get_knowledge

    async def knowledge(*args):
        await entered.wait()
        if child_cancellation:
            raise asyncio.CancelledError("optional cancelled")
        return await original_knowledge(*args)

    async def initialize():
        async with conversation_request_lease(agent):
            await agent._async_initialize_optional_managers()

    async def exclusive():
        async with gate.exclusive():
            maintenance_entered.set()

    try:
        with monkeypatch.context() as patch:
            patch.setattr(agent, "_memory", None)
            patch.setattr(product, "async_get_memory", held_memory)
            patch.setattr(product, "async_get_knowledge", knowledge)
            task = asyncio.create_task(initialize())
            waiter = asyncio.create_task(cleaning.wait())
            await entered.wait()
            if not child_cancellation:
                task.cancel("registration cancelled")
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            assert cleaning.is_set()
            assert not task.done()
            maintenance = asyncio.create_task(exclusive())
            await asyncio.sleep(0)
            assert not maintenance_entered.is_set()
            assert gate._active_readers == 1
            task.cancel("repeat during cleanup")
            await asyncio.sleep(0)
            assert not task.done() and not finished.is_set()
            assert not maintenance_entered.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await maintenance
            assert finished.is_set()
            assert maintenance_entered.is_set()
            assert gate._active_readers == 0
            assert agent._memory is None
        # Shared storage remains usable; cancelling owned initialization did not
        # dispose a manager that a legitimate registration can reuse.
        await initialize()
        assert agent._memory is memory
    finally:
        release.set()
        for pending in (task, maintenance, waiter, *reads):
            if pending is not None:
                pending.cancel()
        await asyncio.gather(
            *(pending for pending in (task, maintenance, waiter, *reads) if pending),
            return_exceptions=True,
        )
