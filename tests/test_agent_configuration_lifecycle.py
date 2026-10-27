"""Behavior of the owned agent lifecycle without installing runtime patches."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_configuration as configuration,
    conversation,
    knowledge,
    memory,
)
from custom_components.extended_openai_conversation_responses.const import (
    SUBSYSTEM_STATUS_KEY,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity as Agent,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)


@pytest.fixture
def startup(monkeypatch):
    """Keep HA registration real at the entity boundary, with controlled storage."""
    options = {
        "memory_mode": "manual",
        "memory_retrieval_mode": "hybrid",
        "memory_embedding_model": "test-model",
        "temporary_memory": "balanced",
        "archive_enabled": True,
        "knowledge_enabled": True,
    }
    agent = Agent(
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent", title="Agent", data=options),
    )
    agent.hass = SimpleNamespace(data={}, config=SimpleNamespace(config_dir="/config"))
    monkeypatch.setattr(
        conversation.ConversationEntity, "async_added_to_hass", AsyncMock()
    )
    monkeypatch.setattr(conversation.conversation, "async_set_agent", Mock())
    monkeypatch.setattr(conversation.SkillManager, "async_get_instance", AsyncMock())
    usage = SimpleNamespace(async_prune_details=AsyncMock())
    persistent = PersistentMemory(
        SimpleNamespace(async_load=AsyncMock(return_value=None)), "agent"
    )
    getters = {}
    for name, result in {
        "usage": usage,
        "guest_mode": object(),
        "request_rules": object(),
        "temporary_memory": object(),
        "archive": SimpleNamespace(async_prune=AsyncMock()),
        "knowledge": object(),
        "memory": persistent,
    }.items():
        getter = getters[name] = AsyncMock(return_value=result)
        monkeypatch.setattr(conversation, f"async_get_{name}", getter)
    for name in (
        "async_get_continuity",
        "reset_function_group_runtime",
        "get_request_rule_runtime",
    ):
        monkeypatch.setattr(conversation, name, Mock())
    scheduled = []
    removals = []

    def track(hass, callback, interval):
        cancel = Mock()
        scheduled.append((hass, callback, interval, cancel))
        return cancel

    monkeypatch.setattr(conversation, "async_track_time_interval", track)
    monkeypatch.setattr(agent, "async_on_remove", removals.append)

    def remove():
        callbacks = removals[:]
        removals.clear()
        for callback in callbacks:
            callback()

    return SimpleNamespace(
        agent=agent,
        getters=getters,
        scheduled=scheduled,
        removals=removals,
        remove=remove,
        memory=persistent,
    )


async def test_startup_publishes_agent_only_after_required_state_is_ready(startup):
    entered = asyncio.Event()
    release = asyncio.Event()
    usage = startup.getters["usage"].return_value

    async def load_usage(*args):
        entered.set()
        await release.wait()
        return usage

    startup.getters["usage"].side_effect = load_usage
    task = asyncio.create_task(startup.agent.async_added_to_hass())
    await entered.wait()
    conversation.conversation.async_set_agent.assert_not_called()
    assert not startup.agent._agent_ready.is_set()
    release.set()
    await task
    conversation.conversation.async_set_agent.assert_called_once()
    assert startup.agent._agent_ready.is_set()
    assert startup.agent._continuity is not None


async def test_startup_registers_one_retention_callback_using_live_settings(startup):
    await startup.agent.async_added_to_hass()
    for getter in startup.getters.values():
        getter.assert_awaited_once()
    assert (
        startup.memory._embedding_provider.create
        == startup.agent._async_create_embeddings
    )
    assert len(startup.scheduled) == 1
    hass, callback, interval, cancel = startup.scheduled[0]
    assert hass is startup.agent.hass
    assert interval == timedelta(days=1)
    assert len(startup.removals) == 2
    assert startup.removals[-1] is cancel
    archive = startup.agent._archive
    archive.async_prune.reset_mock()
    startup.agent.subentry.data = {
        **startup.agent.subentry.data,
        "archive_retention_days": 7,
    }
    await callback(None)
    archive.async_prune.assert_awaited_once_with(7)
    startup.remove()
    cancel.assert_called_once_with()
    assert startup.removals == []
    assert startup.agent._agent_initialization_failed


async def test_reload_rebinds_shared_provider_and_clears_lexical_state(
    startup, monkeypatch
):
    await startup.agent.async_added_to_hass()
    startup.remove()
    replacement = Agent(startup.agent.entry, startup.agent.subentry)
    replacement.hass = startup.agent.hass
    monkeypatch.setattr(replacement, "async_on_remove", startup.removals.append)
    await replacement.async_added_to_hass()
    assert (
        startup.memory._embedding_provider.create
        == replacement._async_create_embeddings
    )
    assert startup.scheduled[0][3].call_count == 1
    assert len(startup.removals) == 2
    replacement.subentry.data = {
        **replacement.subentry.data,
        "memory_retrieval_mode": "lexical",
    }
    startup.remove()
    await replacement.async_added_to_hass()
    assert startup.memory._embedding_provider is None
    assert len(startup.removals) == 2
    startup.remove()
    assert all(scheduled[3].call_count == 1 for scheduled in startup.scheduled)


@pytest.mark.parametrize(
    "failed", ["usage", "temporary_memory", "archive", "knowledge", "memory"]
)
async def test_cancelled_startup_propagates_without_scheduling_retention(
    startup, failed
):
    startup.getters[failed].side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await startup.agent.async_added_to_hass()
    assert startup.scheduled == []
    assert startup.agent._agent_ready.is_set()
    assert startup.agent._agent_initialization_failed
    assert len(startup.removals) == 1
    startup.remove()
    assert startup.removals == []


async def test_required_startup_failure_propagates_before_optional_work(startup):
    startup.getters["usage"].side_effect = OSError("unavailable")
    with pytest.raises(OSError, match="unavailable"):
        await startup.agent.async_added_to_hass()
    startup.getters["memory"].assert_not_awaited()
    assert startup.scheduled == []


@pytest.mark.parametrize("cancel_from_child", [False, True])
async def test_startup_keeps_registration_until_optional_sibling_cleanup_settles(
    startup, cancel_from_child
):
    memory_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    reads = []

    async def load_memory(*_):
        reads.append(asyncio.current_task())
        memory_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    async def load_temporary(*_):
        await memory_started.wait()
        if cancel_from_child:
            raise asyncio.CancelledError("optional initialization cancelled")
        return object()

    startup.getters["memory"].side_effect = load_memory
    startup.getters["temporary_memory"].side_effect = load_temporary
    task = asyncio.create_task(startup.agent.async_added_to_hass())
    cleanup = asyncio.create_task(cleanup_started.wait())
    try:
        await memory_started.wait()
        if not cancel_from_child:
            task.cancel("registration cancelled")
        await asyncio.wait({task, cleanup}, return_when=asyncio.FIRST_COMPLETED)
        assert cleanup_started.is_set()
        assert not task.done()
        assert not startup.agent._agent_ready.is_set()
        task.cancel("repeated cancellation during cleanup")
        await asyncio.sleep(0)
        assert not task.done()
        assert not cleanup_finished.is_set()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        expected = (
            "optional initialization cancelled"
            if cancel_from_child
            else "registration cancelled"
        )
        assert error.value.args == (expected,)
        assert cleanup_finished.is_set()
        assert startup.agent._agent_ready.is_set()
        assert startup.agent._agent_initialization_failed
        assert startup.scheduled == []
        conversation.conversation.async_set_agent.assert_not_called()
    finally:
        release_cleanup.set()
        task.cancel()
        cleanup.cancel()
        for read in reads:
            read.cancel()
        await asyncio.gather(task, cleanup, *reads, return_exceptions=True)


async def test_disabled_archive_prunes_without_enabling_collection_and_search_only_initializes(
    startup,
):
    agent = startup.agent
    agent.subentry.data = {
        **agent.subentry.data,
        "archive_enabled": False,
        "archive_model_search_enabled": False,
    }
    await agent._async_initialize_archive(False)
    assert agent._archive is None
    startup.getters["archive"].assert_awaited_once()
    startup.getters["archive"].return_value.async_prune.assert_awaited_once()
    startup.getters["archive"].reset_mock()
    agent.subentry.data = {**agent.subentry.data, "archive_model_search_enabled": True}
    await agent._async_initialize_archive(False)
    startup.getters["archive"].assert_awaited_once()
    assert agent._archive is not None


async def test_retention_cancellation_is_not_swallowed(startup):
    await startup.agent.async_added_to_hass()
    startup.agent._archive.async_prune.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await startup.scheduled[0][1](None)


async def test_shared_optional_manager_ensure_reuses_existing_without_io(startup):
    """Startup/live ownership must not reacquire an already-loaded shared manager."""
    existing = object()
    startup.agent._temporary_memory = existing
    loader = AsyncMock(side_effect=AssertionError("existing manager must be reused"))

    assert await configuration.async_ensure_optional_manager(
        startup.agent,
        attribute="_temporary_memory",
        subsystem="temporary_memory",
        loader=loader,
        failure_message="unused",
    )
    assert startup.agent._temporary_memory is existing
    loader.assert_not_awaited()
    status = startup.agent.hass.data[SUBSYSTEM_STATUS_KEY][("entry", "agent")][
        "temporary_memory"
    ]
    assert status["status"] == "healthy"


async def test_disabled_knowledge_keeps_distinct_startup_and_live_semantics(
    startup, monkeypatch
):
    """Knowledge stays preloadable at startup but is not live-created while disabled."""
    agent = startup.agent
    agent.subentry.data = {
        **agent.subentry.data,
        "memory_mode": "off",
        "temporary_memory": "off",
        "archive_enabled": False,
        "archive_model_search_enabled": False,
        "knowledge_enabled": False,
    }
    startup_knowledge = object()
    startup.getters["knowledge"].return_value = startup_knowledge

    await agent._async_initialize_optional_managers()
    assert agent._knowledge is startup_knowledge
    startup.getters["knowledge"].assert_awaited_once()

    agent._knowledge = None
    live_getter = AsyncMock(return_value=object())
    monkeypatch.setattr(knowledge, "async_get_knowledge", live_getter)
    await configuration.async_reconcile_runtime_configuration(agent, force=True)

    live_getter.assert_not_awaited()
    assert agent._knowledge is None


async def test_concurrent_reconciliation_initializes_once_and_retries_after_cancellation(
    startup, monkeypatch
):
    agent = startup.agent
    agent.subentry.data = {
        **agent.subentry.data,
        "temporary_memory": "off",
        "archive_enabled": False,
        "archive_model_search_enabled": False,
        "knowledge_enabled": False,
    }
    entered = asyncio.Event()
    release = asyncio.Event()

    async def get_memory(*_args):
        entered.set()
        await release.wait()
        await startup.memory.async_initialize()
        return startup.memory

    getter = AsyncMock(side_effect=get_memory)
    monkeypatch.setattr(memory, "async_get_memory", getter)
    first = asyncio.create_task(
        configuration.async_reconcile_runtime_configuration(agent)
    )
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert agent._memory is None
    release.set()
    await asyncio.gather(
        *(configuration.async_reconcile_runtime_configuration(agent) for _ in range(3))
    )
    assert getter.await_count == 2
    assert agent._memory is startup.memory
    await configuration.async_reconcile_runtime_configuration(agent)
    assert getter.await_count == 2
