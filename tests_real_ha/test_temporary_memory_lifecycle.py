"""Real-HA acceptance coverage for Temporary Memory lifecycle boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_USER,
    TEMPORARY_MEMORY_BALANCED,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockUser
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _speech,
)

_OWNER_ID = "temporary-memory-owner"
_FOREIGN_ID = "temporary-memory-foreign"
_OWNER_SCOPE = f"user:{_OWNER_ID}"
_MEMORY_TEXT = "The temporary launch colour is glacier-teal."
_TOOL_CALL_ID = "call-temporary-memory-add"


def _chat_sse_temporary_memory_add(expires_at: str) -> bytes:
    """Return a real Chat Completions function-call stream for Temporary Memory."""
    chunk = {
        "id": "chatcmpl-temporary-memory-1",
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
                                "name": "temporary_memory_add",
                                "arguments": json.dumps(
                                    {
                                        "content": _MEMORY_TEXT,
                                        "category": "acceptance",
                                        "expires_at": expires_at,
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


async def _agent(hass: HomeAssistant):
    """Load one real conversation agent with user continuity and Temporary Memory."""
    entry = _make_entry(
        "Temporary Memory Lifecycle",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_USER,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._temporary_memory is not None
    return agent


async def _say(
    hass: HomeAssistant,
    agent: Any,
    user_id: str,
    text: str,
    *,
    conversation_id: str | None = None,
) -> conversation.ConversationResult:
    """Enter through Home Assistant's public conversation API as one HA user."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _system_prompt(request_body: dict[str, Any]) -> str:
    """Return the system prompt from one SDK-serialized Chat Completions request."""
    system = next(item for item in request_body["messages"] if item["role"] == "system")
    return str(system["content"])


def _temporary_tool_result(request_body: dict[str, Any]) -> dict[str, Any]:
    """Decode the production Temporary Memory tool result."""
    message = next(
        item
        for item in request_body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == _TOOL_CALL_ID
    )
    outer = json.loads(message["content"])
    return json.loads(outer["result"])


async def test_temporary_memory_created_by_model_is_owned_injected_pruned_and_recovers(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """Model-created temporary context survives only for its owner and active lifetime."""
    MockUser(id=_OWNER_ID, name="Temporary Memory Owner").add_to_hass(hass)
    MockUser(id=_FOREIGN_ID, name="Temporary Memory Foreign User").add_to_hass(hass)
    agent = await _agent(hass)
    temporary = agent._temporary_memory
    assert temporary is not None

    expires_at = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    create_wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_temporary_memory_add(expires_at),
            _chat_sse_text("I will keep that temporarily."),
        ],
    )

    created_turn = await _say(
        hass,
        agent,
        _OWNER_ID,
        "Remember my temporary launch colour for the next hour.",
    )

    assert _speech(created_turn) == "I will keep that temporarily."
    assert len(create_wire.requests) == 2
    assert any(
        tool["function"]["name"] == "temporary_memory_add"
        for tool in create_wire.requests[0]["body"]["tools"]
    )
    created = _temporary_tool_result(create_wire.requests[1]["body"])
    assert created["status"] == "created"
    assert created["memory"]["content"] == _MEMORY_TEXT
    memory_id = created["memory"]["memory_id"]

    owned = await temporary.async_active(_OWNER_SCOPE, owner_scope_id=_OWNER_SCOPE)
    assert [record.memory_id for record in owned] == [memory_id]

    owner_wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Your temporary launch colour is glacier-teal.")],
    )
    owner_turn = await _say(
        hass,
        agent,
        _OWNER_ID,
        "What temporary launch colour are you holding?",
        conversation_id=created_turn.conversation_id,
    )

    assert _speech(owner_turn) == "Your temporary launch colour is glacier-teal."
    assert _MEMORY_TEXT in _system_prompt(owner_wire.requests[0]["body"])

    foreign_wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("I do not have a temporary launch colour for you.")],
    )
    foreign_turn = await _say(
        hass,
        agent,
        _FOREIGN_ID,
        "What temporary launch colour are you holding?",
    )

    assert _speech(foreign_turn) == "I do not have a temporary launch colour for you."
    assert _MEMORY_TEXT not in _system_prompt(foreign_wire.requests[0]["body"])

    # Simulate the clock having crossed the stored absolute expiry without waiting in
    # real time. The next genuine conversation turn must perform production pruning.
    async with temporary._lock:
        current = temporary._records[memory_id]
        temporary._records[memory_id] = replace(
            current,
            expires_at=(dt_util.utcnow() - timedelta(seconds=1)).isoformat(),
        )
        await temporary._async_save_locked()
    pruned_before = temporary.expired_pruned

    expired_wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("That temporary note has expired.")],
    )
    expired_turn = await _say(
        hass,
        agent,
        _OWNER_ID,
        "What temporary launch colour are you holding now?",
        conversation_id=created_turn.conversation_id,
    )

    assert _speech(expired_turn) == "That temporary note has expired."
    assert _MEMORY_TEXT not in _system_prompt(expired_wire.requests[0]["body"])
    assert memory_id not in temporary._records
    assert temporary.expired_pruned == pruned_before + 1

    # The cleanup path must not poison the conversation; a later owner turn still
    # reaches the provider normally with no resurrected temporary context.
    healthy_wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Everything is working normally.")],
    )
    healthy_turn = await _say(
        hass,
        agent,
        _OWNER_ID,
        "Are you still working normally?",
        conversation_id=created_turn.conversation_id,
    )

    assert _speech(healthy_turn) == "Everything is working normally."
    assert _MEMORY_TEXT not in _system_prompt(healthy_wire.requests[0]["body"])
