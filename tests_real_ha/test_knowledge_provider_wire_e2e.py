"""Knowledge acceptance through Home Assistant and the real OpenAI SDK wire."""

from __future__ import annotations

import json
from typing import Any

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_KNOWLEDGE_ENABLED,
)
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _install_wire,
    _response_object,
    _responses_sse_text,
    _speech,
)

_SEARCH_CALL_ID = "call-knowledge-search"
_GET_CALL_ID = "call-knowledge-get"
_RELEVANT_MARKER = "orion-cobalt"
_IRRELEVANT_MARKER = "cedar-amber"


def _chat_sse_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> bytes:
    """Return one real Chat Completions function-call stream."""
    chunk = {
        "id": f"chatcmpl-{call_id}",
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
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, separators=(",", ":")),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _chat_sse_text(text: str) -> bytes:
    """Return one real Chat Completions text stream."""
    chunk = {
        "id": "chatcmpl-knowledge-final",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()


def _responses_sse_tool_call(
    call_id: str, name: str, arguments: dict[str, Any]
) -> bytes:
    """Return one real Responses API function-call stream."""
    encoded = json.dumps(arguments, separators=(",", ":"))
    item = {
        "id": f"fc-{call_id}",
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": encoded,
        "status": "completed",
    }
    events = [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {**item, "arguments": "", "status": "in_progress"},
            "sequence_number": 0,
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": item,
            "sequence_number": 1,
        },
        {
            "type": "response.completed",
            "response": _response_object(f"resp-{call_id}", [item]),
            "sequence_number": 2,
        },
    ]
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


async def _knowledge_agent(hass: HomeAssistant, api_mode: str):
    """Load a genuine conversation agent with Knowledge enabled."""
    entry = _make_entry(
        "Knowledge Provider Wire",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    assert agent._knowledge is not None
    return agent


async def _seed_knowledge(agent: Any):
    """Create one relevant source plus an unrelated source through production CRUD."""
    relevant = await agent._knowledge.async_create(
        "Observatory calibration handbook",
        "Reference procedure for the observatory alignment token",
        (
            "For the September alignment procedure, the observatory calibration "
            f"token is {_RELEVANT_MARKER}. Use this value when confirming alignment."
        ),
    )
    await agent._knowledge.async_create(
        "Garden irrigation notes",
        "Reference notes for the garden watering system",
        f"The irrigation maintenance marker is {_IRRELEVANT_MARKER}.",
    )
    return relevant


async def _say(hass: HomeAssistant, agent: Any, text: str):
    """Enter through Home Assistant's public conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _tool_names(body: dict[str, Any], api_mode: str) -> set[str]:
    """Return provider-visible function names from an SDK-serialized request."""
    if api_mode == API_MODE_RESPONSES:
        return {
            tool["name"]
            for tool in body.get("tools", [])
            if tool.get("type") == "function"
        }
    return {
        tool["function"]["name"]
        for tool in body.get("tools", [])
        if tool.get("type") == "function"
    }


def _chat_tool_result(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Decode one production tool result from a Chat Completions request."""
    tool_message = next(
        item
        for item in body["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == call_id
    )
    outer = json.loads(tool_message["content"])
    return json.loads(outer["result"])


def _responses_tool_result(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Decode one production tool result from a Responses request."""
    tool_item = next(
        item
        for item in body["input"]
        if item.get("type") == "function_call_output" and item.get("call_id") == call_id
    )
    outer = json.loads(tool_item["output"])
    return json.loads(outer["result"])


async def test_chat_knowledge_search_get_and_answer_cross_real_provider_wire(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Join Knowledge discovery, exact-ID get, tool replay and final answer."""
    agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
    relevant = await _seed_knowledge(agent)

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call(
                _SEARCH_CALL_ID,
                "knowledge_search",
                {"query": "observatory calibration token", "limit": 5},
            ),
            _chat_sse_tool_call(
                _GET_CALL_ID,
                "knowledge_get",
                {"source_id": relevant.source_id},
            ),
            _chat_sse_text(f"The observatory calibration token is {_RELEVANT_MARKER}."),
        ],
    )
    result = await _say(hass, agent, "What is the observatory calibration token?")

    assert _speech(result) == f"The observatory calibration token is {_RELEVANT_MARKER}."
    assert [request["path"] for request in wire.requests] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/chat/completions",
    ]

    first = wire.requests[0]["body"]
    assert {"knowledge_search", "knowledge_list", "knowledge_get"}.issubset(
        _tool_names(first, API_MODE_CHAT_COMPLETIONS)
    )
    first_text = json.dumps(first, ensure_ascii=False)
    assert "Knowledge Library" in first_text
    # Knowledge is on-demand: source content itself must not be stuffed into the prompt.
    assert _RELEVANT_MARKER not in first_text
    assert _IRRELEVANT_MARKER not in first_text

    search_result = _chat_tool_result(wire.requests[1]["body"], _SEARCH_CALL_ID)
    assert len(search_result["results"]) == 1
    assert search_result["results"][0]["source_id"] == relevant.source_id
    assert _RELEVANT_MARKER in search_result["results"][0]["excerpt"]
    assert _IRRELEVANT_MARKER not in json.dumps(search_result, ensure_ascii=False)

    get_result = _chat_tool_result(wire.requests[2]["body"], _GET_CALL_ID)
    assert get_result["source_id"] == relevant.source_id
    assert get_result["title"] == "Observatory calibration handbook"
    assert _RELEVANT_MARKER in get_result["content"]
    assert _IRRELEVANT_MARKER not in json.dumps(get_result, ensure_ascii=False)

    # Prove the exact source ID returned by search is the one the provider used for get.
    second_tool_call = next(
        item
        for item in wire.requests[2]["body"]["messages"]
        if item.get("role") == "assistant" and item.get("tool_calls")
    )["tool_calls"][-1]
    assert second_tool_call["function"]["name"] == "knowledge_get"
    assert json.loads(second_tool_call["function"]["arguments"])["source_id"] == relevant.source_id


async def test_responses_knowledge_search_executes_through_real_sdk_wire(
    hass: HomeAssistant, monkeypatch: Any
) -> None:
    """Spot-check provider-visible Knowledge and execution through Responses API."""
    agent = await _knowledge_agent(hass, API_MODE_RESPONSES)
    relevant = await _seed_knowledge(agent)

    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _responses_sse_tool_call(
                _SEARCH_CALL_ID,
                "knowledge_search",
                {"query": "observatory calibration token", "limit": 5},
            ),
            _responses_sse_text(
                f"The observatory calibration token is {_RELEVANT_MARKER}."
            ),
        ],
    )
    result = await _say(hass, agent, "What is the observatory calibration token?")

    assert _speech(result) == f"The observatory calibration token is {_RELEVANT_MARKER}."
    assert [request["path"] for request in wire.requests] == [
        "/v1/responses",
        "/v1/responses",
    ]
    first = wire.requests[0]["body"]
    assert {"knowledge_search", "knowledge_list", "knowledge_get"}.issubset(
        _tool_names(first, API_MODE_RESPONSES)
    )
    assert _RELEVANT_MARKER not in json.dumps(first, ensure_ascii=False)

    search_result = _responses_tool_result(wire.requests[1]["body"], _SEARCH_CALL_ID)
    assert len(search_result["results"]) == 1
    assert search_result["results"][0]["source_id"] == relevant.source_id
    assert _RELEVANT_MARKER in search_result["results"][0]["excerpt"]
    assert _IRRELEVANT_MARKER not in json.dumps(search_result, ensure_ascii=False)
