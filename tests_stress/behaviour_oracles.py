"""Populated fixtures and explicit expected effects for generated configurations."""

from datetime import timedelta
import json
from uuid import uuid4

from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from custom_components.extended_openai_conversation_responses.temporary_memory import (
    async_get_temporary_memory,
)
from homeassistant.util import dt as dt_util
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _response_object,
    _responses_sse_text,
    _responses_sse_tool_call,
)

OWNER = "coverage-owner"
OTHER = "coverage-other"
PRIVATE = "The calibration token is private-cobalt."
SHARED = "The household calibration token is shared-amber."
FOREIGN = "The calibration token is foreign-crimson."
KNOWLEDGE = "The calibration manual token is knowledge-quartz."
TEMPORARY = "The temporary calibration token is temporary-saffron."
RAW_SPEECH = "**alpha** visit https://example.com/guide done."


async def seed_behaviour(hass, entry, subentry):
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    for scope, content in [
        (OWNER, PRIVATE),
        (OTHER, FOREIGN),
        ("shared:household", SHARED),
    ]:
        await memory.async_add(scope, content, "calibration", "explicit")
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    source = await knowledge.async_create(
        "Calibration manual", "Generated fixture", KNOWLEDGE
    )
    temporary = await async_get_temporary_memory(
        hass, entry.entry_id, subentry.subentry_id
    )
    await temporary.async_add(
        f"user:{OWNER}",
        TEMPORARY,
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "calibration",
        owner_scope_id=f"user:{OWNER}",
    )
    return source.source_id


def expected_speech(config):
    """Expected literal transforms; never call production speech processing."""
    if not config["speech_processing_enabled"]:
        return RAW_SPEECH
    word = "beta" if config.get("speech_regex_replacements") else "alpha"
    if not config["speech_strip_markdown"]:
        word = f"**{word}**"
    link = "" if config["speech_strip_urls"] else "https://example.com/guide "
    return f"{word} visit {link}done."


def feature_calls(case, config, source_id):
    """Activate enabled features using independent input choices and fixture IDs."""
    calls = []
    if config["memory_mode"] != "off":
        calls.append(
            (
                "memory_search",
                {"query": "calibration token", "scope": "personal", "limit": 5},
                PRIVATE,
            )
        )
        if config["shared_memory_mode"] != "disabled":
            calls.append(
                (
                    "memory_search",
                    {
                        "query": "household calibration",
                        "scope": "household",
                        "limit": 5,
                    },
                    SHARED,
                )
            )
    if config["knowledge_enabled"]:
        calls.append(
            (
                "knowledge_search",
                {"query": "calibration manual", "source_ids": [source_id], "limit": 3},
                KNOWLEDGE,
            )
        )
    if case["function_tools"] == "direct":
        if case["function_groups"] == "on_demand":
            calls.append(("load_function_groups", {"groups": ["coverage-group"]}, None))
        calls.append(("coverage_marker", {}, None))
    return calls


def provider_reply(api, model, streaming, name=None, arguments=None, index=0):
    """Emit real SDK wire replies for both streaming and completed models."""
    call_id = f"call-generated-{uuid4().hex}"
    if streaming:
        if name:
            reply = (
                _responses_sse_tool_call(
                    call_id=call_id, name=name, tool_arguments=arguments
                )
                if api == "responses"
                else _chat_sse_tool_call(
                    call_id=call_id, name=name, arguments=arguments
                )
            )
        else:
            reply = (
                _responses_sse_text(RAW_SPEECH)
                if api == "responses"
                else _chat_sse_text(RAW_SPEECH)
            )
        return reply.replace(b"gpt-5.6", model.encode())
    if api == "responses":
        if name:
            output = [
                {
                    "type": "function_call",
                    "id": f"fc-generated-{index}",
                    "call_id": call_id,
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg-generated",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": RAW_SPEECH,
                            "annotations": [],
                            "logprobs": [],
                        }
                    ],
                }
            ]
        result = _response_object(f"resp-generated-{index}", output)
        result["model"] = model
    else:
        message = {"role": "assistant", "content": None if name else RAW_SPEECH}
        if name:
            message["tool_calls"] = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        result = {
            "id": f"chat-generated-{index}",
            "object": "chat.completion",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if name else "stop",
                }
            ],
        }
    return 200, result


def last_tool_result(body, api):
    history = body["input" if api == "responses" else "messages"]
    item = next(
        item
        for item in reversed(history)
        if item.get("type") == "function_call_output" or item.get("role") == "tool"
    )
    outer = json.loads(item.get("output", item.get("content")))
    if isinstance(outer.get("result"), str):
        try:
            return json.loads(outer["result"])
        except json.JSONDecodeError:
            return outer
    return outer


def assert_feature_results(requests, calls, api):
    for index, (name, arguments, expected) in enumerate(calls):
        result = last_tool_result(requests[index + 1]["body"], api)
        serialized = json.dumps(result)
        assert FOREIGN not in serialized
        if expected:
            assert expected in serialized, (name, result)
        elif name == "memory_search":
            assert PRIVATE not in serialized and SHARED not in serialized
        elif name == "knowledge_search":
            assert KNOWLEDGE not in serialized
        elif name == "load_function_groups":
            assert sorted(result["loaded"] + result["already_loaded"]) == sorted(
                arguments["groups"]
            )
