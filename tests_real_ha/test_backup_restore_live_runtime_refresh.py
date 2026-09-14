"""Real-HA coverage for successful backup restore refreshing live runtime state."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_USER,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    async_get_temporary_memory,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockUser
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
)
from tests_real_ha.test_provider_wire_e2e import _install_wire, _speech

_USER_ID = "live-restore-user"
_OWNER_SCOPE = f"user:{_USER_ID}"
_TARGET_MEMORY = "Restored persistent marker is delta-saffron."
_CURRENT_MEMORY = "Current persistent marker is stale-violet."
_TARGET_TEMPORARY = "Restored temporary marker is glacier-mint."
_CURRENT_TEMPORARY = "Current temporary marker is stale-copper."
_TARGET_KNOWLEDGE = "restored-orbit-cobalt"
_CURRENT_KNOWLEDGE = "stale-orbit-amber"
_SEARCH_CALL_ID = "call-restored-knowledge-search"


def _system_prompt(body: dict[str, Any]) -> str:
    """Return the SDK-serialized system prompt from one Chat Completions request."""
    message = next(item for item in body["messages"] if item.get("role") == "system")
    return str(message["content"])


def _knowledge_tool_result(body: dict[str, Any]) -> dict[str, Any]:
    """Decode the production Knowledge tool result from one provider request."""
    message = next(
        item
        for item in body["messages"]
        if item.get("role") == "tool"
        and item.get("tool_call_id") == _SEARCH_CALL_ID
    )
    outer = json.loads(message["content"])
    return json.loads(outer["result"])


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public Conversation API as the test user."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=agent.entry.entry_id,
    )


@pytest.mark.asyncio
async def test_successful_loaded_restore_refreshes_live_subsystem_objects_and_turns(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful restore is immediately effective in the loaded conversation runtime."""
    MockUser(id=_USER_ID, name="Live Restore User").add_to_hass(hass)

    entry = _make_entry(
        "Live Backup Restore Runtime",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    await _setup_entry(hass, entry)
    agent_before = conversation.async_get_agent(hass, entry.entry_id)
    assert agent_before is not None
    subentry = agent_before.subentry

    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    temporary = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)

    assert agent_before._memory is memory
    assert agent_before._temporary_memory is temporary
    assert agent_before._knowledge is knowledge

    target_memory = await memory.async_add(
        _OWNER_SCOPE,
        _TARGET_MEMORY,
        "acceptance",
        "explicit",
    )
    target_memory_id = target_memory["memory"]["memory_id"]

    target_temporary = await temporary.async_add(
        _OWNER_SCOPE,
        _TARGET_TEMPORARY,
        (dt_util.utcnow() + timedelta(hours=4)).isoformat(),
        "acceptance",
        owner_scope_id=_OWNER_SCOPE,
    )
    target_temporary_id = target_temporary["memory"]["memory_id"]

    target_knowledge = await knowledge.async_create(
        "Restored runtime handbook",
        "Reference containing the restored runtime marker.",
        f"The restored runtime knowledge marker is {_TARGET_KNOWLEDGE}.",
    )

    # Capture the complete desired state while the entry and conversation entity are
    # genuinely loaded. The restore later has to make this snapshot effective in the
    # active runtime, not merely write equivalent bytes to Home Assistant storage.
    target_snapshot = await backup.async_collect_backup_snapshot(hass, entry, subentry)

    assert await memory.async_delete(_OWNER_SCOPE, [target_memory_id]) == 1
    current_memory = await memory.async_add(
        _OWNER_SCOPE,
        _CURRENT_MEMORY,
        "acceptance",
        "explicit",
    )
    current_memory_id = current_memory["memory"]["memory_id"]

    assert await temporary.async_delete(
        _OWNER_SCOPE,
        [target_temporary_id],
        owner_scope_id=_OWNER_SCOPE,
    ) == 1
    current_temporary = await temporary.async_add(
        _OWNER_SCOPE,
        _CURRENT_TEMPORARY,
        (dt_util.utcnow() + timedelta(hours=4)).isoformat(),
        "acceptance",
        owner_scope_id=_OWNER_SCOPE,
    )
    current_temporary_id = current_temporary["memory"]["memory_id"]

    assert await knowledge.async_delete(target_knowledge.source_id)
    current_knowledge = await knowledge.async_create(
        "Current stale runtime handbook",
        "Reference that must disappear after restore.",
        f"The pre-restore runtime marker is {_CURRENT_KNOWLEDGE}.",
    )

    assert [item.memory_id for item in await memory.async_list(_OWNER_SCOPE)] == [
        current_memory_id
    ]
    assert [
        item.memory_id
        for item in await temporary.async_active(
            _OWNER_SCOPE, owner_scope_id=_OWNER_SCOPE
        )
    ] == [current_temporary_id]
    assert (await knowledge.async_search(_CURRENT_KNOWLEDGE))[0].source_id == (
        current_knowledge.source_id
    )

    restored = await backup.async_restore_backup(hass, entry, subentry, target_snapshot)
    assert restored["status"] == "restored"
    await hass.async_block_till_done()

    # A restore may keep the exact conversation entity or cause Home Assistant to
    # recreate it as configuration persistence settles. Either outcome is valid; the
    # currently registered entity must point at the canonical restored managers.
    live_agent = conversation.async_get_agent(hass, entry.entry_id)
    assert live_agent is not None
    live_memory = await async_get_memory(
        hass, entry.entry_id, live_agent.subentry.subentry_id
    )
    live_temporary = await async_get_temporary_memory(
        hass, entry.entry_id, live_agent.subentry.subentry_id
    )
    live_knowledge = await async_get_knowledge(
        hass, entry.entry_id, live_agent.subentry.subentry_id
    )

    assert live_agent._memory is live_memory
    assert live_agent._temporary_memory is live_temporary
    assert live_agent._knowledge is live_knowledge

    memories = await live_agent._memory.async_list(_OWNER_SCOPE)
    assert [(item.memory_id, item.content) for item in memories] == [
        (target_memory_id, _TARGET_MEMORY)
    ]
    assert all(item.memory_id != current_memory_id for item in memories)

    temporary_records = await live_agent._temporary_memory.async_active(
        _OWNER_SCOPE, owner_scope_id=_OWNER_SCOPE
    )
    assert [(item.memory_id, item.content) for item in temporary_records] == [
        (target_temporary_id, _TARGET_TEMPORARY)
    ]
    assert all(item.memory_id != current_temporary_id for item in temporary_records)

    knowledge_results = await live_agent._knowledge.async_search(_TARGET_KNOWLEDGE)
    assert len(knowledge_results) == 1
    assert knowledge_results[0].source_id == target_knowledge.source_id
    assert _TARGET_KNOWLEDGE in knowledge_results[0].excerpt
    assert await live_agent._knowledge.async_search(_CURRENT_KNOWLEDGE) == []

    # Finally exercise the restored objects through the actual loaded conversation
    # entity. Temporary Memory must already be injected into the next provider prompt,
    # and Knowledge must answer through the model-facing production tool immediately.
    wire = _install_wire(
        monkeypatch,
        live_agent,
        [
            _chat_sse_tool_call(
                _SEARCH_CALL_ID,
                "knowledge_search",
                {"query": "restored runtime knowledge marker", "limit": 5},
            ),
            _chat_sse_text(
                f"The restored knowledge marker is {_TARGET_KNOWLEDGE}."
            ),
        ],
    )

    result = await _say(
        hass,
        live_agent,
        "What is the restored runtime knowledge marker?",
    )

    assert _speech(result) == f"The restored knowledge marker is {_TARGET_KNOWLEDGE}."
    assert len(wire.requests) == 2

    first_body = wire.requests[0]["body"]
    prompt = _system_prompt(first_body)
    assert _TARGET_TEMPORARY in prompt
    assert _CURRENT_TEMPORARY not in prompt

    tool_result = _knowledge_tool_result(wire.requests[1]["body"])
    assert len(tool_result["results"]) == 1
    assert tool_result["results"][0]["source_id"] == target_knowledge.source_id
    assert _TARGET_KNOWLEDGE in tool_result["results"][0]["excerpt"]
    assert _CURRENT_KNOWLEDGE not in json.dumps(tool_result, ensure_ascii=False)

    # No restart/reload is performed by the test itself. At this point both direct
    # runtime access and a public Conversation turn agree on the restored state.
    assert live_agent.entry.state.name == "LOADED"
