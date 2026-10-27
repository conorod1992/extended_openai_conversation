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


async def _native_agent(hass):
    return await _agent(
        hass,
        title="Independent optional cancellation probe",
        memory_mode="manual",
        temporary_memory="balanced",
        knowledge_enabled=True,
        archive_enabled=True,
    )


def _observe_drain(monkeypatch):
    """Observe the existing drain boundary after the sibling-cancel pass."""
    drain_entered = asyncio.Event()
    original = product.async_wait_owned_cleanup

    async def observe(task):
        drain_entered.set()
        return await original(task)

    monkeypatch.setattr(product, "async_wait_owned_cleanup", observe)
    return drain_entered


async def _initialize_in_real_lease(agent):
    async with conversation_request_lease(agent):
        await agent._async_initialize_optional_managers()


async def test_already_cancelling_sibling_finishes_cleanup_before_owner_returns(
    hass, monkeypatch
):
    """A second child's cancellation must not re-cancel a draining sibling."""
    agent = await _native_agent(hass)
    gate = get_agent_maintenance_gate(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    memory, temporary = agent._memory, agent._temporary_memory
    cleaning = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    interrupted = asyncio.Event()
    children = []
    memory_children = []

    async def memory_loader(*_args):
        child = asyncio.current_task()
        children.append(child)
        memory_children.append(child)
        child.cancel("memory independently cancelled")
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                interrupted.set()
                raise
            finished.set()

    async def temporary_loader(*_args):
        children.append(asyncio.current_task())
        await cleaning.wait()
        raise asyncio.CancelledError("temporary independently cancelled")

    task = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(agent, "_memory", None)
            patch.setattr(agent, "_temporary_memory", None)
            patch.setattr(product, "async_get_memory", memory_loader)
            patch.setattr(product, "async_get_temporary_memory", temporary_loader)
            drain_entered = _observe_drain(patch)
            task = asyncio.create_task(_initialize_in_real_lease(agent))
            await drain_entered.wait()
            # The observer is reached after task.cancel() queues the sibling's
            # wakeup. One scheduler turn lets that already-queued wakeup run.
            await asyncio.sleep(0)
            returned_before_release = False
            if interrupted.is_set():
                with pytest.raises(asyncio.CancelledError):
                    await task
                returned_before_release = True
            evidence = {
                "owner_returned_before_release": returned_before_release,
                "cleanup_interrupted": interrupted.is_set(),
                "cleanup_finished": finished.is_set(),
                "active_readers_before_release": gate._active_readers,
                "memory_cancellation_requests": memory_children[0].cancelling(),
            }
            assert not interrupted.is_set(), evidence
            assert not task.done(), evidence
            assert gate._active_readers == 1, evidence
            release.set()
            with pytest.raises(
                asyncio.CancelledError, match="temporary independently cancelled"
            ):
                await task
            assert finished.is_set()
            assert all(child.done() for child in children)
            assert gate._active_readers == 0
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        agent._memory, agent._temporary_memory = memory, temporary


@pytest.mark.parametrize("cancel_source", ["caller", "child"])
async def test_cleanup_failure_preserves_original_startup_cancellation(
    hass, monkeypatch, cancel_source
):
    """An optional loader finalizer failure must not replace original cancellation."""
    agent = await _native_agent(hass)
    gate = get_agent_maintenance_gate(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    memory, temporary = agent._memory, agent._temporary_memory
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleanup_failed = asyncio.Event()
    children = []
    expected = (
        "registration original cancellation"
        if cancel_source == "caller"
        else "temporary original cancellation"
    )

    async def memory_loader(*_args):
        children.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleanup_failed.set()
            raise OSError("controlled optional cleanup failure")

    async def temporary_loader(*_args):
        children.append(asyncio.current_task())
        await entered.wait()
        if cancel_source == "child":
            raise asyncio.CancelledError(expected)
        return temporary

    task = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(agent, "_memory", None)
            patch.setattr(agent, "_temporary_memory", None)
            patch.setattr(product, "async_get_memory", memory_loader)
            patch.setattr(product, "async_get_temporary_memory", temporary_loader)
            drain_entered = _observe_drain(patch)
            task = asyncio.create_task(_initialize_in_real_lease(agent))
            await entered.wait()
            if cancel_source == "caller":
                task.cancel(expected)
            await drain_entered.wait()
            await cleaning.wait()
            for index in range(3):
                task.cancel(f"later caller cancellation {index}")
                await asyncio.sleep(0)
                assert not task.done()
                assert not cleanup_failed.is_set()
                assert gate._active_readers == 1
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == (expected,)
            assert cleanup_failed.is_set()
            assert all(child.done() for child in children)
            assert gate._active_readers == 0
            assert agent._memory is None
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        agent._memory, agent._temporary_memory = memory, temporary
