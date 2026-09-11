"""Memory acceptance through Home Assistant and the real OpenAI SDK wire."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from custom_components.extended_openai_conversation_responses import (
    continuity as continuity_module,
    conversation as conversation_module,
    memory as memory_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_MODE,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_USER,
    MEMORY_MODE_MANUAL,
    TEMPORARY_MEMORY_BALANCED,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    TemporaryMemoryRecord,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockUser
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _responses_sse_text,
    _speech,
)

_USER_ID = "memory-provider-wire-user"
_FOREIGN_USER_ID = "memory-provider-wire-foreign"
_TEMP_SCOPE = f"user:{_USER_ID}"
_FOREIGN_TEMP_SCOPE = f"user:{_FOREIGN_USER_ID}"
_TOOL_CALL_ID = "call-memory-provider-wire"
_NEW_MEMORY = "The user's project codename is lattice-quartz."


def _chat_sse_memory_add() -> bytes:
    """Return one real Chat Completions function-call stream for memory_add."""
    chunk = {
        "id": "chatcmpl-memory-provider-wire-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": _TOOL_CALL_ID,
                            "type": "function",
                            "function": {
                                "name": "memory_add",
                                "arguments": json.dumps(
                                    {
                                        "content": _NEW_MEMORY,
                                        "category": "projects",
                                        "source": "explicit",
                                    },
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


async def _memory_agent(hass: HomeAssistant, api_mode: str):
    """Load a genuine conversation agent with both Memory systems enabled."""
    entry = _make_entry(
        "Memory Provider Wire",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._memory is not None
    assert agent._temporary_memory is not None
    return entry, agent


async def _say(
    hass: HomeAssistant,
    agent: Any,
    text: str,
    *,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public conversation API as the test user."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=_USER_ID),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _request_text(body: dict[str, Any]) -> str:
    """Flatten the SDK-serialized request only for inclusion/exclusion assertions."""
    return json.dumps(body, ensure_ascii=False)


def _memory_tool_result(body: dict[str, Any]) -> dict[str, Any]:
    """Decode the production Memory tool result from the second provider request."""
    tool_message = next(
        item
        for item in body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == _TOOL_CALL_ID
    )
    outer = json.loads(tool_message["content"])
    return json.loads(outer["result"])


async def _seed_memories(agent: Any) -> None:
    """Create relevant, irrelevant and differently-owned durable facts."""
    assert agent._memory is not None
    await agent._memory.async_add(
        _USER_ID,
        "The user's calibration token is cobalt-zebra.",
        "preferences",
        "explicit",
    )
    await agent._memory.async_add(
        _USER_ID,
        "The user's favourite soup is minestrone.",
        "food",
        "explicit",
    )
    await agent._memory.async_add(
        _FOREIGN_USER_ID,
        "The user's calibration token is amber-lynx.",
        "preferences",
        "explicit",
    )


async def _seed_temporary_memories(agent: Any) -> None:
    """Create one active owned fact plus expired and foreign exclusions."""
    temporary = agent._temporary_memory
    assert temporary is not None
    now = dt_util.utcnow()
    await temporary.async_add(
        _TEMP_SCOPE,
        "Temporary owned marker is active-saffron.",
        (now + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=_TEMP_SCOPE,
    )
    await temporary.async_add(
        _FOREIGN_TEMP_SCOPE,
        "Temporary foreign marker is foreign-violet.",
        (now + timedelta(hours=1)).isoformat(),
        "acceptance",
        owner_scope_id=_FOREIGN_TEMP_SCOPE,
    )
    expired = TemporaryMemoryRecord(
        memory_id="expired-memory-provider-wire",
        scope_id=_TEMP_SCOPE,
        content="Temporary expired marker is expired-crimson.",
        category="acceptance",
        source="automatic",
        expires_at=(now - timedelta(minutes=1)).isoformat(),
        created_at=(now - timedelta(hours=1)).isoformat(),
        updated_at=(now - timedelta(hours=1)).isoformat(),
        owner_scope_id=_TEMP_SCOPE,
    )
    # Normal production writes reject a past expiry. Seed only that impossible state
    # directly so the request still exercises production prune/filter/inject behavior.
    async with temporary._lock:
        temporary._records[expired.memory_id] = expired


async def _fresh_agent_from_durable_memory(hass: HomeAssistant, entry: Any) -> Any:
    """Cross a restart-like boundary while retaining only durable Memory storage."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    hass.data.pop(memory_module._MEMORY_MANAGERS, None)
    hass.data.pop(continuity_module._MANAGERS, None)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._memory is not None
    return agent


async def test_chat_memory_crosses_provider_tool_and_durable_retrieval_boundaries(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Memory retrieval, model write, tool replay and restart retrieval stay joined."""
    MockUser(id=_USER_ID, name="Memory Provider Wire User", is_owner=True).add_to_hass(
        hass
    )
    entry, agent = await _memory_agent(hass, API_MODE_CHAT_COMPLETIONS)
    await _seed_memories(agent)
    await _seed_temporary_memories(agent)

    # Pinpoint the temporary-memory seam without bypassing production retrieval.
    # The snapshot proves the fixture is visible under the intended scope while
    # leaving the deliberately expired record in place for the real request to prune.
    seeded_active = await agent._temporary_memory.async_active_snapshot(_TEMP_SCOPE)
    assert [item.content for item in seeded_active] == [
        "Temporary owned marker is active-saffron."
    ]
    runtime_scopes: list[str | None] = []
    retrieved_temporary: list[list[str]] = []
    original_retrieve_temporary = agent._async_retrieve_temporary_memories

    async def _capture_temporary_retrieval() -> list[TemporaryMemoryRecord]:
        runtime_scopes.append(conversation_module._ACTIVE_TEMPORARY_SCOPE.get())
        records = await original_retrieve_temporary()
        retrieved_temporary.append([item.content for item in records])
        return records

    monkeypatch.setattr(
        agent, "_async_retrieve_temporary_memories", _capture_temporary_retrieval
    )

    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_memory_add(), _chat_sse_text("I remembered the project codename.")],
    )
    result = await _say(
        hass,
        agent,
        "What is my calibration token? Remember that my project codename is lattice-quartz.",
    )

    assert runtime_scopes == [_TEMP_SCOPE]
    assert retrieved_temporary == [["Temporary owned marker is active-saffron."]]
    assert _speech(result) == "I remembered the project codename."
    assert [request["path"] for request in wire.requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]
    first_request = _request_text(wire.requests[0]["body"])
    assert "cobalt-zebra" in first_request
    assert "minestrone" not in first_request
    assert "amber-lynx" not in first_request
    assert "active-saffron" in first_request
    assert "expired-crimson" not in first_request
    assert "foreign-violet" not in first_request
    assert any(
        tool["function"]["name"] == "memory_add"
        for tool in wire.requests[0]["body"]["tools"]
    )

    tool_result = _memory_tool_result(wire.requests[1]["body"])
    assert tool_result["status"] == "created"
    assert tool_result["memory"]["content"] == _NEW_MEMORY

    # Discard both the live manager and its continuity bundle. The next manager must
    # reconstruct the provider-written fact from Home Assistant's durable Store.
    reloaded_agent = await _fresh_agent_from_durable_memory(hass, entry)
    stored = await reloaded_agent._memory.async_search(
        _USER_ID, "lattice-quartz project codename"
    )
    assert [item.content for item in stored] == [_NEW_MEMORY]

    later_wire = _install_wire(
        monkeypatch,
        reloaded_agent,
        [_chat_sse_text("Your project codename is lattice-quartz.")],
    )
    later = await _say(hass, reloaded_agent, "What is my project codename?")

    assert _speech(later) == "Your project codename is lattice-quartz."
    assert len(later_wire.requests) == 1
    later_request = _request_text(later_wire.requests[0]["body"])
    assert "lattice-quartz" in later_request
    assert "amber-lynx" not in later_request


async def test_responses_memory_prompt_injection_uses_real_sdk_wire(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Spot-check the same owned retrieval contract through the Responses API."""
    MockUser(id=_USER_ID, name="Memory Responses User", is_owner=True).add_to_hass(hass)
    _entry, agent = await _memory_agent(hass, API_MODE_RESPONSES)
    await _seed_memories(agent)

    wire = _install_wire(
        monkeypatch,
        agent,
        [_responses_sse_text("Your calibration token is cobalt-zebra.")],
    )
    result = await _say(hass, agent, "What is my calibration token?")

    assert _speech(result) == "Your calibration token is cobalt-zebra."
    assert [request["path"] for request in wire.requests] == ["/v1/responses"]
    request = _request_text(wire.requests[0]["body"])
    assert "cobalt-zebra" in request
    assert "minestrone" not in request
    assert "amber-lynx" not in request
