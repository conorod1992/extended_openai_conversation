"""Public request entries own speculative retrieval across nested invocations."""

from dataclasses import replace

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    conversation as lifecycle,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_TEMPORARY_MEMORY,
    TEMPORARY_MEMORY_BALANCED,
)


async def _call(agent, request, direct):
    if direct:
        result, metadata = await agent.async_process_direct(request)
        assert metadata == {"handled_locally": False}
        return result
    return await agent.async_process(request)


class Retrievals:
    """Gate real retrieval entry points without replacing their task ownership."""

    def __init__(self, monkeypatch, same_agent):
        self.request = ContextVar("test_prefetch_request", default=None)
        self.started = {name: asyncio.Event() for name in ("parent", "child")}
        self.release = {name: asyncio.Event() for name in ("parent", "child")}
        self.cancelled = {name: asyncio.Event() for name in ("parent", "child")}
        self.tasks = set()
        self.loads = []
        self.operation = None
        self.parent = self._agent()
        self.child = self.parent if same_agent else self._agent()
        monkeypatch.setattr(lifecycle, "memory_enabled", lambda _data: True)
        monkeypatch.setattr(
            lifecycle, "sync_memory_embedding_provider", lambda _agent: None
        )
        monkeypatch.setattr(
            lifecycle, "_owner_from_resolved_scope", lambda _scope: "owner"
        )
        monkeypatch.setattr(
            lifecycle, "_ACTIVE_TEMPORARY_SCOPE", SimpleNamespace(get=lambda: "session")
        )

    def _agent(self):
        agent = object.__new__(lifecycle.ExtendedOpenAIAgentEntity)
        agent.subentry = SimpleNamespace(
            data={CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED}
        )
        agent._temporary_memory = object()

        async def select(*_args):
            return []

        async def load():
            request = self.request.get()
            self.loads.append(request)
            self.started[request].set()
            try:
                await self.release[request].wait()
            except asyncio.CancelledError:
                self.cancelled[request].set()
                raise
            return [f"{request} fact"]

        async def core(request):
            token = self.request.set(request)
            try:
                return await self.operation(agent, request)
            finally:
                self.request.reset(token)

        agent._async_select_memories = select
        agent._async_load_temporary_memories = load
        agent._effective_guest_policy = lambda: SimpleNamespace(
            temporary_memory=self.request.get() != "child-off"
        )
        agent._async_process = core
        return agent

    async def prepare(self, agent):
        assert await agent._async_retrieve_memories("context", "query") == []
        task = lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch
        assert task is not None
        self.tasks.add(task)
        return task

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        return task

    async def close(self):
        for event in self.release.values():
            event.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


@pytest.fixture
async def retrievals(monkeypatch, same_agent):
    state = Retrievals(monkeypatch, same_agent)
    token = lifecycle._ACTIVE_INVOCATION.set(
        replace(lifecycle._ACTIVE_INVOCATION.get(), temporary_memory_prefetch=None)
    )
    try:
        yield state
    finally:
        await state.close()
        lifecycle._ACTIVE_INVOCATION.reset(token)


@pytest.mark.parametrize("same_agent", [False, True])
@pytest.mark.parametrize("direct", [False, True])
async def test_disabled_nested_request_does_not_cancel_parent_prefetch(
    retrievals, direct
):
    state = retrievals

    async def process(agent, request):
        if request == "child-off":
            assert await agent._async_retrieve_temporary_memories() == []
            return "child without temporary memory"
        parent_task = await state.prepare(agent)
        await state.started["parent"].wait()
        assert (
            await _call(state.child, "child-off", direct)
            == "child without temporary memory"
        )
        assert not parent_task.cancelled(), "The child cancelled its parent's retrieval"
        assert (
            lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is parent_task
        )
        state.release["parent"].set()
        assert await agent._async_retrieve_temporary_memories() == ["parent fact"]
        return "parent completed"

    state.operation = process
    assert await _call(state.parent, "parent", direct) == "parent completed"
    assert state.loads == ["parent"]
    assert lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is None


@pytest.mark.parametrize("same_agent", [False, True])
@pytest.mark.parametrize("direct", [False, True])
async def test_enabled_nested_request_consumes_only_its_own_prefetch(
    retrievals, direct
):
    state = retrievals
    child_prepared = asyncio.Event()
    observed = {}

    async def release_retrievals():
        await child_prepared.wait()
        state.release["parent"].set()
        state.release["child"].set()

    async def process(agent, request):
        task = await state.prepare(agent)
        observed[request] = task
        if request == "child":
            child_prepared.set()
            return await agent._async_retrieve_temporary_memories()
        await state.started["parent"].wait()
        state.spawn(release_retrievals())
        child_facts = await _call(state.child, "child", direct)
        assert observed["child"] is not task, "The child reused its parent's prefetch"
        assert child_facts == ["child fact"]
        assert lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is task
        assert await agent._async_retrieve_temporary_memories() == ["parent fact"]
        return "parent completed"

    state.operation = process
    assert await _call(state.parent, "parent", direct) == "parent completed"
    assert sorted(state.loads) == ["child", "parent"]
    assert lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is None


@pytest.mark.parametrize("same_agent", [False, True])
@pytest.mark.parametrize("direct", [False, True])
async def test_child_cancellation_preserves_parent_prefetch(retrievals, direct):
    state = retrievals
    child_prepared = asyncio.Event()
    observed = {}

    async def process(agent, request):
        task = await state.prepare(agent)
        observed[request] = task
        if request == "child":
            child_prepared.set()
            return await agent._async_retrieve_temporary_memories()
        await state.started["parent"].wait()
        child = state.spawn(_call(state.child, "child", direct))
        await child_prepared.wait()
        child.cancel()
        with pytest.raises(asyncio.CancelledError):
            await child
        assert not task.cancelled(), (
            "Cancelling the child cancelled the parent prefetch"
        )
        assert lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is task
        assert observed["child"] is not task
        assert observed["child"].cancelled()
        state.release["parent"].set()
        assert await agent._async_retrieve_temporary_memories() == ["parent fact"]
        return "parent completed"

    state.operation = process
    assert await _call(state.parent, "parent", direct) == "parent completed"
    assert not state.cancelled["parent"].is_set()
    assert lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is None


@pytest.mark.parametrize("same_agent", [False, True])
@pytest.mark.parametrize("direct", [False, True])
async def test_parent_cancellation_leaves_separately_owned_child_running(
    retrievals, direct
):
    state = retrievals
    child_prepared = asyncio.Event()
    observed = {}
    child_requests = []

    async def process(agent, request):
        task = await state.prepare(agent)
        observed[request] = task
        if request == "child":
            child_prepared.set()
            return await agent._async_retrieve_temporary_memories()
        await state.started["parent"].wait()
        child_requests.append(state.spawn(_call(state.child, "child", direct)))
        await asyncio.Event().wait()

    state.operation = process
    parent = state.spawn(_call(state.parent, "parent", direct))
    await child_prepared.wait()
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert observed["parent"].cancelled(), "The exited request retained its prefetch"
    assert observed["child"] is not observed["parent"]
    assert not child_requests[0].done()
    assert not observed["child"].cancelled()
    state.release["child"].set()
    assert await child_requests[0] == ["child fact"]
    assert state.cancelled["parent"].is_set()
    assert not state.cancelled["child"].is_set()
    assert lifecycle._ACTIVE_INVOCATION.get().temporary_memory_prefetch is None
