"""An inherited request cannot wait on its ancestor's active ChatLog."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONVERSATION_CONTINUITY_HA_DEFAULT,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_orchestration import _pipeline_fixture


@pytest.mark.parametrize("target", ["same", "new-id", "other-agent"])
async def test_processing_scope_rejects_ancestor_lock_without_affecting_controls(
    monkeypatch, target
):
    agent, request, chat_log, policy, process = _pipeline_fixture(monkeypatch)
    parent = ConversationContinuity("parent")
    other = ConversationContinuity("other")
    scope = user_scope("alice", source="test")
    agent._continuity = parent
    claim = await parent.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT, scope, None, chat_log.conversation_id, 30
    )
    child_manager = other if target == "other-agent" else parent
    child_id = "new-conversation" if target == "new-id" else chat_log.conversation_id

    async def nested(*args):
        child = await asyncio.create_task(
            child_manager.async_resolve(
                CONVERSATION_CONTINUITY_HA_DEFAULT, scope, None, child_id, 30
            )
        )
        await child_manager.async_release(child.key, child.claim_token)
        return SimpleNamespace(
            response=SimpleNamespace(error_code=None), conversation_id=child_id
        )

    agent._async_handle_message_with_ha_tools = nested
    try:
        if target == "same":
            with pytest.raises(HomeAssistantError, match="[Nn]ested.*conversation"):
                await asyncio.wait_for(process(), 1)
        else:
            await asyncio.wait_for(process(), 1)
        assert parent._ha_default_lock_users[chat_log.conversation_id] == 1
    finally:
        await parent.async_release(claim.key, claim.claim_token)
    # After the enclosing request exits, the same ID remains usable.
    child = await parent.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT, scope, None, chat_log.conversation_id, 30
    )
    await parent.async_release(child.key, child.claim_token)
    assert not parent._ha_default_locks


async def test_descendant_outliving_processing_scope_can_use_released_id(monkeypatch):
    agent, request, chat_log, policy, process = _pipeline_fixture(monkeypatch)
    manager = ConversationContinuity("parent")
    agent._continuity = manager
    scope = user_scope("alice", source="test")
    claim = await manager.async_resolve(
        CONVERSATION_CONTINUITY_HA_DEFAULT, scope, None, chat_log.conversation_id, 30
    )
    resume = asyncio.Event()
    tasks = []

    async def child():
        await resume.wait()
        result = await manager.async_resolve(
            CONVERSATION_CONTINUITY_HA_DEFAULT,
            scope,
            None,
            chat_log.conversation_id,
            30,
        )
        await manager.async_release(result.key, result.claim_token)

    async def launch(*args):
        tasks.append(asyncio.create_task(child()))
        return SimpleNamespace(
            response=SimpleNamespace(error_code=None),
            conversation_id=chat_log.conversation_id,
        )

    agent._async_handle_message_with_ha_tools = launch
    try:
        await process()
    finally:
        await manager.async_release(claim.key, claim.claim_token)
        resume.set()
    await asyncio.wait_for(asyncio.gather(*tasks), 1)
    assert not manager._ha_default_locks
