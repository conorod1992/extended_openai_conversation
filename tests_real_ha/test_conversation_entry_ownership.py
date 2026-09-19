"""Genuine HA setup/reload and public request-entry ownership acceptance."""

from __future__ import annotations

from unittest.mock import AsyncMock

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
