"""Rejected provider output preserves completed files and valid recovery history."""

import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.ha_tool_result_compat import (
    is_tool_result_content,
    tool_result_data,
)
from homeassistant.components import conversation
from homeassistant.helpers.chat_session import async_get_chat_session
from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
    _response_object,
    _responses_sse_text,
    _responses_sse_tool_call,
)


def _repeated_output(mode, malformed):
    if mode == "chat_completions":
        chunk = json.loads(
            _chat_sse_tool_call("same", "unavailable", {}).split(b"\n\n")[0][6:]
        )
        events = [chunk]
        if malformed:
            events.append(
                {
                    **chunk,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "Invalid tail"},
                            "finish_reason": None,
                        }
                    ],
                }
            )
        events.append(
            {
                **chunk,
                "choices": [],
                "usage": {
                    "prompt_tokens": 223,
                    "completion_tokens": 17,
                    "total_tokens": 240,
                },
            }
        )
        return (
            "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()
            + b"data: [DONE]\n\n"
        )
    reasoning = {
        "id": "rs-current",
        "type": "reasoning",
        "summary": [],
        "encrypted_content": "encrypted",
    }
    call = {
        "id": "fc-current",
        "type": "function_call",
        "call_id": "same",
        "name": "unavailable",
        "arguments": "{}",
        "status": "completed",
    }
    outputs = [reasoning, call]
    if malformed:
        outputs.append(
            {
                "id": "msg-current",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "Progress", "annotations": []}
                ],
            }
        )
    events = []
    for index, item in enumerate(outputs):
        events.append(
            {"type": "response.output_item.added", "output_index": index, "item": item}
        )
        if item["type"] == "message":
            events.append(
                {
                    "type": "response.output_text.delta",
                    "output_index": index,
                    "content_index": 0,
                    "delta": "Progress",
                }
            )
        events.append(
            {"type": "response.output_item.done", "output_index": index, "item": item}
        )
    response = _response_object("repeated-output", outputs)
    response["usage"] = (
        None
        if malformed
        else {"input_tokens": 223, "output_tokens": 17, "total_tokens": 240}
    )
    events.append({"type": "response.completed", "response": response})
    if malformed:
        events.extend(
            [
                {
                    "type": "response.output_text.delta",
                    "output_index": 2,
                    "content_index": 0,
                    "delta": "Invalid tail",
                },
                {
                    "type": "response.usage",
                    "usage": {
                        "input_tokens": 223,
                        "output_tokens": 17,
                        "total_tokens": 240,
                    },
                },
            ]
        )
    return "".join(
        f"data: {json.dumps({**event, 'sequence_number': i})}\n\n"
        for i, event in enumerate(events)
    ).encode()


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("malformed", [False, True])
async def test_repeated_output_cleanup_preserves_file_and_safe_followup(
    hass, monkeypatch, mode, malformed
):
    from custom_components.extended_openai_conversation_responses import (
        context_usage_hardening as hardening,
    )
    from custom_components.extended_openai_conversation_responses.functions import (
        file as files,
    )

    monkeypatch.setattr(hardening, "_TRAILING_USAGE_TIMEOUT", 5)
    root = Path(hass.config.config_dir) / DOMAIN
    root.mkdir(parents=True, exist_ok=True)
    (root / "sub").mkdir()
    target = root / "target.txt"
    backend = {
        "type": "write_file",
        "path": "{{ filename | default('target.txt') }}",
        "content": "changed",
    }
    tools = [
        {
            "spec": {
                "name": name,
                "description": "Write a file",
                "parameters": {
                    "type": "object",
                    "properties": {"filename": {"type": "string"}},
                },
            },
            "function": backend,
        }
        for name in ("write", "write_alias")
    ]
    agent = await _agent(
        hass, api_mode=mode, functions=tools, function_tool_error_recovery=False
    )
    writes = []
    atomic_write = files._atomic_replace_text

    def write(path, content, **kwargs):
        result = atomic_write(path, content, **kwargs)
        writes.append(path)
        return result

    monkeypatch.setattr(files, "_atomic_replace_text", write)
    call = _responses_sse_tool_call if mode == "responses" else _chat_sse_tool_call
    text = _responses_sse_text if mode == "responses" else _chat_sse_text
    _install_wire(monkeypatch, agent, [text("Ready")])
    seed = await _say(hass, agent, "hello")
    wire = _install_wire(
        monkeypatch,
        agent,
        [call("same", "write", {}), _repeated_output(mode, malformed)],
    )
    failed = await _say(hass, agent, "write", seed.conversation_id)
    assert failed.response.error_code is not None
    assert len(wire.requests) == 2
    assert target.read_text(encoding="utf-8") == "changed"
    assert writes == [target]
    assert agent._usage.requests[-1].total_tokens == 240
    with async_get_chat_session(hass, failed.conversation_id) as session:
        with conversation.async_get_chat_log(hass, session) as log:
            calls = [
                call
                for item in log.content
                if isinstance(item, conversation.AssistantContent)
                for call in item.tool_calls or []
            ]
            results = [item for item in log.content if is_tool_result_content(item)]
            assert len(calls) == len(results) == 1
            original = calls[0]
            assert original.tool_name == "write"
            assert tool_result_data(results[0])["result"]["success"] is True
            assert agent._execution_tool_outcomes[id(original)] == "completed"
            receipt = agent._execution_effect_receipts[id(original)][0]
            assert (receipt.kind, receipt.outcome) == ("file_write", "completed")
            assert agent._unacknowledged_effects[failed.conversation_id]
            if mode == "responses":
                natives = [
                    getattr(item.native, "id", None)
                    for item in log.content
                    if isinstance(item, conversation.AssistantContent)
                ]
                assert "fc-provider-wire" in natives
                assert "rs-current" not in natives and "fc-current" not in natives
    _install_wire(
        monkeypatch,
        agent,
        [call("retry", "write_alias", {"filename": "./sub/../target.txt"})],
    )
    blocked = await _say(hass, agent, "retry", failed.conversation_id)
    assert blocked.response.error_code is not None
    assert writes == [target]
    wire = _install_wire(monkeypatch, agent, [text("Acknowledged")])
    recovered = await _say(hass, agent, "acknowledge", failed.conversation_id)
    assert _speech(recovered) == "Acknowledged"
    outgoing = wire.requests[0]["body"]["input" if mode == "responses" else "messages"]
    if mode == "responses":
        call_ids = [
            item["call_id"] for item in outgoing if item.get("type") == "function_call"
        ]
        result_ids = [
            item["call_id"]
            for item in outgoing
            if item.get("type") == "function_call_output"
        ]
        assert not any(item.get("id") == "rs-current" for item in outgoing)
    else:
        call_ids = [
            call["id"] for item in outgoing for call in item.get("tool_calls", [])
        ]
        result_ids = [
            item["tool_call_id"] for item in outgoing if item.get("role") == "tool"
        ]
    assert len(set(call_ids)) == len(call_ids)
    assert sorted(call_ids) == sorted(result_ids)
    _install_wire(
        monkeypatch, agent, [call("intentional", "write_alias", {}), text("Done")]
    )
    repeated = await _say(hass, agent, "repeat intentionally", failed.conversation_id)
    assert _speech(repeated) == "Done"
    assert writes == [target, target]


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_finalizer_cannot_acknowledge_a_file_with_a_repeated_call_id(
    hass, monkeypatch, mode
):
    root = Path(hass.config.config_dir) / DOMAIN
    root.mkdir(parents=True, exist_ok=True)
    tool = {
        "spec": {
            "name": "write",
            "description": "Write",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {
            "type": "write_file",
            "path": "finalizer.txt",
            "content": "changed",
        },
    }
    agent = await _agent(
        hass,
        api_mode=mode,
        functions=[tool],
        continue_conversation="conditional",
        function_tool_error_recovery=False,
    )
    call = _responses_sse_tool_call if mode == "responses" else _chat_sse_tool_call
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            call("same", "write", {}),
            call(
                "same",
                "set_continue_conversation",
                {"response": "Done", "continue_conversation": False},
            ),
        ],
    )
    result = await _say(hass, agent, "write")
    assert result.response.error_code is not None
    assert len(wire.requests) == 2
    assert (root / "finalizer.txt").read_text(encoding="utf-8") == "changed"
    assert agent._unacknowledged_effects[result.conversation_id]
    with async_get_chat_session(hass, result.conversation_id) as session:
        with conversation.async_get_chat_log(hass, session) as log:
            calls = [
                call
                for item in log.content
                if isinstance(item, conversation.AssistantContent)
                for call in item.tool_calls or []
            ]
            results = [item for item in log.content if is_tool_result_content(item)]
            assert len(calls) == len(results) == 1
            assert calls[0].tool_name == "write"
            assert tool_result_data(results[0])["result"]["success"] is True
