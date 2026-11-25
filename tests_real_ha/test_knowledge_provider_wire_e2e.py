"""Knowledge acceptance through Home Assistant and the real OpenAI SDK wire."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_GUEST_KNOWLEDGE_POLICY,
    CONF_GUEST_KNOWLEDGE_SOURCE_IDS,
    CONF_GUEST_POLICY_VERSION,
    CONF_KNOWLEDGE_ENABLED,
    GUEST_POLICY_VERSION,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    update_live_subentry,
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


def _chat_sse_tool_call(
    call_id: str, name: str, arguments: Any = None, *, raw_arguments: str | None = None
) -> bytes:
    """Return one real Chat Completions function-call stream."""
    encoded = (
        json.dumps(arguments, separators=(",", ":"))
        if raw_arguments is None
        else raw_arguments
    )
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
                                "arguments": encoded,
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
    call_id: str, name: str, arguments: Any = None, *, raw_arguments: str | None = None
) -> bytes:
    """Return one real Responses API function-call stream."""
    encoded = (
        json.dumps(arguments, separators=(",", ":"))
        if raw_arguments is None
        else raw_arguments
    )
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


async def _knowledge_agent(hass: HomeAssistant, api_mode: str, **options: Any):
    """Load a genuine conversation agent with Knowledge enabled."""
    entry = _make_entry(
        "Knowledge Provider Wire",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_KNOWLEDGE_ENABLED: True,
            **options,
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


async def _say(
    hass: HomeAssistant, agent: Any, text: str, *, user_id: str | None = None
):
    """Enter through Home Assistant's public conversation API."""
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=None,
        context=Context(user_id=user_id),
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
    result = json.loads(tool_message["content"])["result"]
    return json.loads(result) if isinstance(result, str) else result


def _responses_tool_result(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    """Decode one production tool result from a Responses request."""
    tool_item = next(
        item
        for item in body["input"]
        if item.get("type") == "function_call_output" and item.get("call_id") == call_id
    )
    result = json.loads(tool_item["output"])["result"]
    return json.loads(result) if isinstance(result, str) else result


def _chat_tool_arguments(
    body: dict[str, Any], call_id: str, name: str
) -> dict[str, Any]:
    """Return the arguments of one exact assistant tool call from serialized history."""
    call = next(
        call
        for item in body["messages"]
        if item.get("role") == "assistant"
        for call in item.get("tool_calls", [])
        if call.get("id") == call_id
        and call.get("function", {}).get("name") == name
    )
    return json.loads(call["function"]["arguments"])


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
    assert search_result["results"][0]["start_character"] == 0
    assert _RELEVANT_MARKER in search_result["results"][0]["excerpt"]
    assert _IRRELEVANT_MARKER not in json.dumps(search_result, ensure_ascii=False)

    get_result = _chat_tool_result(wire.requests[2]["body"], _GET_CALL_ID)
    assert get_result["source_id"] == relevant.source_id
    assert get_result["title"] == "Observatory calibration handbook"
    assert _RELEVANT_MARKER in get_result["content"]
    assert _IRRELEVANT_MARKER not in json.dumps(get_result, ensure_ascii=False)

    # Search returns the ID that the provider then sends back through knowledge_get.
    get_arguments = _chat_tool_arguments(
        wire.requests[2]["body"], _GET_CALL_ID, "knowledge_get"
    )
    assert get_arguments["source_id"] == search_result["results"][0]["source_id"]


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
    assert search_result["results"][0]["start_character"] == 0
    assert _RELEVANT_MARKER in search_result["results"][0]["excerpt"]
    assert _IRRELEVANT_MARKER not in json.dumps(search_result, ensure_ascii=False)


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_knowledge_search_requires_array_source_filter_at_provider_boundary(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, api_mode: str
) -> None:
    agent = await _knowledge_agent(hass, api_mode, function_tool_error_recovery=True)
    allowed = await _seed_knowledge(agent)
    blocked = await agent._knowledge.async_create(
        "Private observatory handbook",
        "Reference for the observatory calibration token",
        "Observatory calibration token is private-orion.",
    )
    update_live_subentry(
        hass,
        agent.entry,
        agent.subentry,
        data={
            **agent.subentry.data,
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_KNOWLEDGE_POLICY: "custom",
            CONF_GUEST_KNOWLEDGE_SOURCE_IDS: [allowed.source_id],
        },
    )
    await agent._guest_mode.async_update_trusted(indefinite=True)

    chat = api_mode == API_MODE_CHAT_COMPLETIONS
    call = _chat_sse_tool_call if chat else _responses_sse_tool_call
    text = _chat_sse_text if chat else _responses_sse_text
    decode = _chat_tool_result if chat else _responses_tool_result
    for source_ids in ("", " \t", "0", None, [], ["0"], [blocked.source_id]):
        wire = _install_wire(
            monkeypatch,
            agent,
            [
                call(
                    _SEARCH_CALL_ID,
                    "knowledge_search",
                    {"query": "observatory calibration token", "source_ids": source_ids},
                ),
                text("Search complete"),
            ],
        )
        assert _speech(await _say(hass, agent, "Check the calibration token")) == (
            "Search complete"
        )
        result = decode(wire.requests[1]["body"], _SEARCH_CALL_ID)
        if source_ids is None or isinstance(source_ids, str):
            assert result.get("code") == "invalid_arguments", (source_ids, result)
            assert "source_ids" in result["error"]
        else:
            assert [item["source_id"] for item in result["results"]] == (
                [] if source_ids else [allowed.source_id]
            )
        assert "private-orion" not in json.dumps(result)


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("arguments", [None, [], "calibration token"], ids=["null", "array", "string"])
async def test_nonobject_knowledge_arguments_follow_error_recovery(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, api_mode: str,
    recovery: bool, arguments: Any,
) -> None:
    agent = await _knowledge_agent(hass, api_mode, function_tool_error_recovery=recovery)
    await _seed_knowledge(agent)
    search = AsyncMock()
    monkeypatch.setattr(agent, "_async_execute_knowledge_tool", search)
    chat = api_mode == API_MODE_CHAT_COMPLETIONS
    call = _chat_sse_tool_call if chat else _responses_sse_tool_call
    text = _chat_sse_text if chat else _responses_sse_text
    decode = _chat_tool_result if chat else _responses_tool_result
    replies = [call(_SEARCH_CALL_ID, "knowledge_search", arguments)]
    if recovery:
        replies.append(text("Recovered"))
    wire = _install_wire(monkeypatch, agent, replies)

    result = await _say(hass, agent, "Check the calibration token")
    search.assert_not_awaited()
    if recovery:
        assert _speech(result) == "Recovered"
        assert len(wire.requests) == 2
        feedback = decode(wire.requests[1]["body"], _SEARCH_CALL_ID)
        assert feedback["code"] == "invalid_arguments"
        assert feedback["stage"] == "pre_dispatch_validation"
        assert feedback["error"] == "Function input must be an object"
    else:
        assert len(wire.requests) == 1
        assert result.response.error_code is not None
        assert "Function input must be an object" in result.response.as_dict()["speech"]["plain"]["speech"]


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("recovery_enabled", [False, True])
@pytest.mark.parametrize(
    "arguments",
    [
        '{"value":' + "9" * 5000 + "}",
        '{"query":"calibration","nested":[{"value":NaN}]}',
        '{"query":"calibration","nested":[{"value":Infinity}]}',
        '{"query":"calibration","nested":[{"value":-Infinity}]}',
        '{"query":"calibration","nested":[{"value":1e400}]}',
        r'{"query":"calibration","nested":[{"value":"\ud800"}]}',
        r'{"query":"calibration","nested":[{"\udfff":"value"}]}',
    ],
    ids=[
        "oversized-int", "nan", "infinity", "negative-infinity", "float-overflow",
        "surrogate-value", "surrogate-key",
    ],
)
async def test_invalid_provider_json_arguments_follow_error_recovery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    recovery_enabled: bool,
    arguments: str,
) -> None:
    agent = await _knowledge_agent(
        hass, api_mode, function_tool_error_recovery=recovery_enabled
    )
    await _seed_knowledge(agent)
    search = AsyncMock()
    monkeypatch.setattr(agent, "_async_execute_knowledge_tool", search)
    chat = api_mode == API_MODE_CHAT_COMPLETIONS
    call = _chat_sse_tool_call if chat else _responses_sse_tool_call
    text = _chat_sse_text if chat else _responses_sse_text
    replies = [call(_SEARCH_CALL_ID, "knowledge_search", raw_arguments=arguments)]
    if recovery_enabled:
        replies.append(text("Recovered"))
    wire = _install_wire(monkeypatch, agent, replies)

    result = await _say(hass, agent, "Check the calibration token")

    search.assert_not_awaited()
    if not recovery_enabled:
        assert len(wire.requests) == 1
        assert result.response.error_code is not None
        speech = result.response.as_dict()["speech"]["plain"]["speech"]
        assert "unparseable tool-call arguments" in speech
        return

    assert _speech(result) == "Recovered"
    assert len(wire.requests) == 2
    continuation = wire.requests[1]["body"]
    if chat:
        feedback = _chat_tool_result(continuation, _SEARCH_CALL_ID)
        replayed = next(
            call["function"]["arguments"]
            for message in continuation["messages"]
            for call in message.get("tool_calls") or []
            if call["id"] == _SEARCH_CALL_ID
        )
    else:
        feedback = _responses_tool_result(continuation, _SEARCH_CALL_ID)
        replayed = next(
            item["arguments"]
            for item in continuation["input"]
            if item.get("type") == "function_call" and item["call_id"] == _SEARCH_CALL_ID
        )
    assert feedback["code"] == "invalid_json"
    assert feedback["stage"] == "provider_argument_decoding"
    assert replayed == arguments


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize("recovery_enabled", [False, True])
@pytest.mark.parametrize(
    ("tool_name", "handler"),
    [
        ("knowledge_search", "_async_execute_knowledge_tool"),
        ("memory_search", "_async_execute_memory_tool"),
        ("conversation_search", "_async_execute_archive_tool"),
    ],
)
async def test_whitespace_search_query_is_rejected_before_spending_execution_slot(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    recovery_enabled: bool,
    tool_name: str,
    handler: str,
) -> None:
    agent = await _knowledge_agent(
        hass,
        api_mode,
        function_tool_error_recovery=recovery_enabled,
        max_function_calls_per_conversation=1,
        memory_mode=MEMORY_MODE_MANUAL,
        archive_enabled=True,
        archive_model_search_enabled=True,
    )
    await _seed_knowledge(agent)
    user = await hass.auth.async_create_user("Search owner", group_ids=["system-admin"])
    search = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(agent, handler, search)
    chat = api_mode == API_MODE_CHAT_COMPLETIONS
    call = _chat_sse_tool_call if chat else _responses_sse_tool_call
    text = _chat_sse_text if chat else _responses_sse_text
    call_id = "call-blank-query"
    corrected_query = " calibration token "
    replies = [call(call_id, tool_name, {"query": " \t\n\u2003"})]
    if recovery_enabled:
        replies.extend(
            [
                call("call-corrected-query", tool_name, {"query": corrected_query}),
                text("Recovered"),
            ]
        )
    wire = _install_wire(monkeypatch, agent, replies)

    result = await _say(hass, agent, "Check past notes", user_id=user.id)

    assert tool_name in _tool_names(wire.requests[0]["body"], api_mode)
    if not recovery_enabled:
        search.assert_not_awaited()
        assert len(wire.requests) == 1
        assert result.response.error_code is not None
        speech = result.response.as_dict()["speech"]["plain"]["speech"]
        assert "query must not be blank" in speech
        return

    assert _speech(result) == "Recovered"
    assert len(wire.requests) == 3
    decode = _chat_tool_result if chat else _responses_tool_result
    feedback = decode(wire.requests[1]["body"], call_id)
    assert feedback["code"] == "invalid_arguments"
    assert feedback["stage"] == "pre_dispatch_validation"
    assert feedback["error"] == "query must not be blank"
    search.assert_awaited_once()
    assert search.await_args.args[0] == "search"
    assert search.await_args.args[1]["query"] == corrected_query
