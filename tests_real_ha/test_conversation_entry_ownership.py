"""Genuine HA setup/reload and public request-entry ownership acceptance."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    agent_maintenance,
    conversation as agent_module,
    debug,
    ha_permissions,
    request_static_cache,
    voice_identity_runtime,
)
from custom_components.extended_openai_conversation_responses.const import (
    DOMAIN,
    SERVICE_PROCESS,
)
from custom_components.extended_openai_conversation_responses.ha_llm_tools import (
    ToolSnapshot,
    current_snapshot,
    tool_snapshot_scope,
)
from custom_components.extended_openai_conversation_responses.request_origin import (
    bind_request_satellite,
    get_request_satellite,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


async def test_request_entry_identity_and_public_paths_survive_real_setup_reload(
    hass, hass_admin_user, monkeypatch
):
    """Real startup/reload preserves ownership and both APIs complete without a model."""
    cls = agent_module.ExtendedOpenAIAgentEntity
    names = (
        "async_process",
        "async_process_direct",
        "_async_process",
        "_async_process_with_continuity",
        "_async_process_claimed",
        "_execute_function_tool",
        "_async_dispatch_function_tool",
        "_async_retrieve_memories",
        "_async_select_memories",
        "_async_rank_memories",
        "_async_retrieve_temporary_memories",
        "_async_load_temporary_memories",
        "_async_execute_memory_tool",
        "_async_execute_temporary_memory_tool",
        "_async_execute_archive_tool",
        "_async_execute_knowledge_tool",
    )
    base_executor = agent_module.ExtendedOpenAIBaseLLMEntity._execute_function_tool
    owners = {name: getattr(cls, name) for name in names}
    helpers = (
        agent_maintenance.conversation_request_lease,
        debug.conversation_debug_trace,
        request_static_cache.formatted_tool_cache,
        voice_identity_runtime.voice_identity_scope,
    )
    entry = _make_entry(
        "Owned Conversation Entry", include_ai_task=False, local_intents=True
    )
    await _setup_entry(hass, entry)
    caller = Context(user_id=hass_admin_user.id)
    outer = Context(user_id="outside-request")
    with ha_permissions.bind_active_ha_context(outer):
        for reloaded in (False, True):
            if reloaded:
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
            assert (
                agent_module.ExtendedOpenAIBaseLLMEntity._execute_function_tool
                is base_executor
            )
            for name, method in owners.items():
                assert getattr(cls, name) is method
                assert not hasattr(method, "__wrapped__")
            assert helpers == (
                agent_maintenance.conversation_request_lease,
                debug.conversation_debug_trace,
                request_static_cache.formatted_tool_cache,
                voice_identity_runtime.voice_identity_scope,
            )
            agent = conversation.async_get_agent(hass, entry.entry_id)
            assert isinstance(agent, cls)
            provider = AsyncMock(
                side_effect=AssertionError("Local request reached provider")
            )
            monkeypatch.setattr(agent, "_async_handle_message_with_ha_tools", provider)
            manager = debug.get_debug_manager(
                hass, entry.entry_id, agent.subentry.subentry_id
            )
            manager.configure(enabled=True)
            before = manager.status()["count"]
            result = await conversation.async_converse(
                hass=hass,
                text="what time is it",
                conversation_id=None,
                context=caller,
                language="en",
                agent_id=entry.entry_id,
            )
            assert result.response.error_code is None
            assert result.conversation_id
            assert ha_permissions.get_active_ha_context() is outer
            direct = await hass.services.async_call(
                DOMAIN,
                SERVICE_PROCESS,
                {
                    "agent_id": agent.entity_id,
                    "text": "what time is it",
                    "language": "en",
                },
                blocking=True,
                return_response=True,
                context=caller,
            )
            assert direct["handled_locally"] is True
            assert direct["response"]
            assert direct["conversation_id"]
            assert ha_permissions.get_active_ha_context() is outer
            assert manager.status()["count"] == before + 2
            provider.assert_not_awaited()


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("outcome", ["raised", "cancelled"])
async def test_snapshot_boundary_precedes_maintenance_and_restores_failed_entries(
    hass, hass_admin_user, monkeypatch, direct, outcome
):
    """Fault lower preparation seams while keeping the real entry owners/lease."""
    from tests_real_ha.test_cross_feature_acceptance import _agent

    agent = await _agent(hass)
    parent = ToolSnapshot(
        caller_provided=True, caller_prompt="parent", unavailable_sources=["retain"]
    )
    parent_tools = parent.tools
    outer_context, child_context = Context(), Context(user_id=hass_admin_user.id)
    request = conversation.ConversationInput(
        text="snapshot failure",
        context=child_context,
        conversation_id=None,
        device_id=None,
        satellite_id="assist_satellite.child",
        language="en",
        agent_id=agent.entity_id,
    )
    entered, hold = asyncio.Event(), asyncio.Event()
    snapshots, restored = [], []
    original_lease = agent_module.conversation_request_lease
    failure = RuntimeError("snapshot preparation failed")

    @asynccontextmanager
    async def inspect_lease(entity):
        snapshot = current_snapshot()
        snapshots.append(snapshot)
        assert snapshot is not parent and not snapshot.caller_provided
        assert snapshot.tools == {} and snapshot.unavailable_sources == []
        assert snapshot.caller_prompt == ""
        assert ha_permissions.get_active_ha_context() is child_context
        assert get_request_satellite() == "assist_satellite.child"
        async with original_lease(entity):
            yield

    async def fail_preparation(_hass, _data):
        entered.set()
        assert current_snapshot() is snapshots[0]
        if outcome == "raised":
            raise failure
        await hold.wait()

    monkeypatch.setattr(agent_module, "conversation_request_lease", inspect_lease)
    monkeypatch.setattr(
        agent_module, "async_prepare_runtime_function_tools", fail_preparation
    )

    async def invoke():
        try:
            method = agent.async_process_direct if direct else agent.async_process
            await method(request)
        finally:
            restored.append(
                (
                    current_snapshot(),
                    ha_permissions.get_active_ha_context(),
                    get_request_satellite(),
                )
            )
            entered.set()

    with (
        tool_snapshot_scope(parent),
        ha_permissions.bind_active_ha_context(outer_context),
        bind_request_satellite("assist_satellite.parent"),
    ):
        task = asyncio.create_task(invoke())
        await entered.wait()
        if outcome == "cancelled":
            task.cancel("snapshot cancellation")
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ("snapshot cancellation",)
        else:
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is failure
        assert current_snapshot() is parent
    assert len(restored) == 1
    assert restored[0][0] is parent and restored[0][1] is outer_context
    assert restored[0][2] == "assist_satellite.parent"
    assert parent.tools is parent_tools and parent.tools == {}
    assert parent.caller_prompt == "parent" and parent.unavailable_sources == ["retain"]
    gate = agent_maintenance.get_agent_maintenance_gate(
        hass, agent.entry.entry_id, agent.subentry.subentry_id
    )
    assert (
        gate._active_readers == 0
        and gate._reader_depth == {}
        and gate._reader_tasks == {}
    )
