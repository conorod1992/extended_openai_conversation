"""Provider protocol acceptance with independently validated retained history."""

from copy import deepcopy
import json

import httpx
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
)
from tests_real_ha.test_provider_wire_e2e import (
    _agent,
    _chat_sse_text,
    _responses_sse_text,
)


def _events(body):
    return [
        json.loads(part.removeprefix("data: "))
        for part in body.decode().split("\n\n")
        if part and part != "data: [DONE]"
    ]


def _stream(events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def _assert_valid_outgoing_history(body, api_mode):
    """The provider rejects histories with missing, duplicate or ambiguous results."""
    pending = set()
    seen = set()
    for message in body[
        "messages" if api_mode == API_MODE_CHAT_COMPLETIONS else "input"
    ]:
        calls = message.get("tool_calls", [])
        if message.get("type") == "function_call":
            calls = [{"id": message["call_id"], "function": message}]
        for call in calls:
            call_id = call["id"]
            assert call_id and call_id not in seen
            seen.add(call_id)
            pending.add(call_id)
            json.loads(call["function"]["arguments"])
        if (
            message.get("role") == "tool"
            or message.get("type") == "function_call_output"
        ):
            call_id = message.get("tool_call_id", message.get("call_id"))
            assert call_id in pending
            pending.remove(call_id)
        elif message.get("role") == "user":
            assert not pending
    assert not pending


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
@pytest.mark.parametrize(
    "fault",
    [
        "after-terminal",
        "duplicate-id",
        "conflicting-id",
        "null",
        "array",
        "string",
        "number",
        "loader-null",
        "loader-array",
        "loader-string",
        "loader-number",
        "invalid-json",
    ],
)
async def test_malformed_tool_round_recovers_with_valid_same_conversation_history(
    hass, monkeypatch, api_mode, fault
):
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_tool_call,
        _raw_client,
        _responses_sse_tool_call,
        _speech,
    )

    agent = await _agent(hass, api_mode)
    control_name = (
        "load_function_groups"
        if fault.startswith("loader-")
        else "set_continue_conversation"
    )
    fault = fault.removeprefix("loader-")
    values = {"null": None, "array": [], "string": "wrong", "number": 7}
    if api_mode == API_MODE_CHAT_COMPLETIONS:
        events = _events(_chat_sse_tool_call())
        call = events[0]["choices"][0]["delta"]["tool_calls"][0]
        if fault in values:
            call["function"] = {
                "name": control_name,
                "arguments": json.dumps(values[fault]),
            }
        elif fault == "invalid-json":
            call["function"]["arguments"] = "{broken"
        elif fault == "duplicate-id":
            from copy import deepcopy

            other = deepcopy(call)
            other["index"] = 1
            events[0]["choices"][0]["delta"]["tool_calls"].append(other)
        elif fault == "conflicting-id":
            from copy import deepcopy

            other = deepcopy(events[0])
            events[0]["choices"][0]["finish_reason"] = None
            other["choices"][0]["delta"]["tool_calls"][0]["id"] = "conflicting-call"
            events.append(other)
        else:
            events = _events(_chat_sse_text("Terminal")) + events
    else:
        events = _events(_responses_sse_tool_call())
        if fault in values:
            for event in events[:2]:
                event["item"]["name"] = control_name
            events[1]["item"]["arguments"] = json.dumps(values[fault])
        elif fault == "invalid-json":
            events[1]["item"]["arguments"] = "{broken"
        elif fault == "duplicate-id":
            from copy import deepcopy

            other = deepcopy(events[:2])
            for event in other:
                event["output_index"] = 1
                event["item"]["id"] = "second-item"
            events = events[:2] + other + events[2:]
        elif fault == "conflicting-id":
            events[1]["item"]["call_id"] = "conflicting-call"
        else:
            events = [
                *_events(_responses_sse_text("Terminal")),
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 1,
                    "item_id": "late-action",
                    "delta": "{}",
                    "sequence_number": 5,
                },
            ]
    replies = [
        _responses_sse_text("Session established")
        if api_mode == API_MODE_RESPONSES
        else _chat_sse_text("Session established"),
        _stream(events),
        _responses_sse_text("Recovered")
        if api_mode == API_MODE_RESPONSES
        else _chat_sse_text("Recovered"),
    ]
    requests = []
    effects = []

    async def action(call):
        effects.append(dict(call.data))

    hass.services.async_register("light", "turn_off", action)

    async def send(request, *args, **kwargs):
        body = json.loads(request.content)
        _assert_valid_outgoing_history(body, api_mode)
        requests.append(body)
        assert len(requests) <= len(replies)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=replies[len(requests) - 1],
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)

    async def turn(conversation_id):
        return await conversation.async_converse(
            hass=hass,
            text="protocol probe",
            conversation_id=conversation_id,
            context=Context(),
            language="en",
            agent_id=agent.entry.entry_id,
        )

    established = await turn(None)
    assert established.conversation_id
    failed = await turn(established.conversation_id)
    assert failed.response.error_code is not None
    if fault in values or fault == "invalid-json":
        assert agent._usage.runs[-1].error_type == "ParseArgumentsFailed"
    assert effects == []
    recovered = await turn(failed.conversation_id)
    assert _speech(recovered) == "Recovered"
    assert recovered.conversation_id == failed.conversation_id
    assert len(requests) == 3
    assert effects == []


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_interleaved_fragmented_tools_and_trailing_usage_keep_exact_effects(
    hass, monkeypatch, api_mode
):
    from homeassistant.components import conversation
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
    )
    from homeassistant.core import Context
    from tests_real_ha.test_provider_wire_e2e import (
        _chat_sse_tool_call,
        _raw_client,
        _responses_sse_tool_call,
        _speech,
    )

    agent = await _agent(hass, api_mode)
    targets = ["light.protocol_kitchen", "light.protocol_hall"]
    calls = []
    for index, target in enumerate(targets):
        hass.states.async_set(target, "on")
        async_expose_entity(hass, conversation.DOMAIN, target, True)
        calls.append(
            (
                f"call-interleaved-{index}",
                {
                    "list": [
                        {
                            "domain": "light",
                            "service": "turn_off",
                            "service_data": {"entity_id": [target]},
                        }
                    ]
                },
            )
        )
    effects = []

    async def action(call):
        effects.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("light", "turn_off", action)
    if api_mode == API_MODE_CHAT_COMPLETIONS:
        template = _events(_chat_sse_tool_call())[0]
        events = []
        fragments = [json.dumps(args) for _, args in calls]
        for part in range(3):
            event = deepcopy(template)
            event["choices"][0]["finish_reason"] = "tool_calls" if part == 2 else None
            deltas = []
            for index in [1, 0]:
                raw = fragments[index]
                start, end = len(raw) * part // 3, len(raw) * (part + 1) // 3
                delta = {"index": index, "function": {"arguments": raw[start:end]}}
                if part == 0:
                    delta.update(id=calls[index][0], type="function")
                    delta["function"]["name"] = "execute_services"
                deltas.append(delta)
            event["choices"][0]["delta"] = {"tool_calls": deltas}
            events.append(event)
        events.append(
            {
                **template,
                "choices": [],
                "usage": {
                    "prompt_tokens": 17,
                    "completion_tokens": 9,
                    "total_tokens": 26,
                },
            }
        )
    else:
        items = []
        events = []
        for index, (call_id, args) in enumerate(calls):
            source = _events(
                _responses_sse_tool_call(call_id=call_id, tool_arguments=args)
            )
            item = source[1]["item"]
            item["id"] = f"fc-interleaved-{index}"
            items.append(item)
            events.append(
                {
                    "type": "response.output_item.added",
                    "output_index": index,
                    "item": {**item, "arguments": "", "status": "in_progress"},
                    "sequence_number": len(events),
                }
            )
        for part in range(3):
            for index in [1, 0]:
                raw = items[index]["arguments"]
                events.append(
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": items[index]["id"],
                        "output_index": index,
                        "delta": raw[len(raw) * part // 3 : len(raw) * (part + 1) // 3],
                        "sequence_number": len(events),
                    }
                )
        for index in [1, 0]:
            events.append(
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": items[index]["id"],
                    "output_index": index,
                    "name": "execute_services",
                    "arguments": items[index]["arguments"],
                    "sequence_number": len(events),
                }
            )
            events.append(
                {
                    "type": "response.output_item.done",
                    "item": items[index],
                    "output_index": index,
                    "sequence_number": len(events),
                }
            )
        from tests_real_ha.test_provider_wire_e2e import _response_object

        response = _response_object("resp-interleaved", items)
        events.append(
            {
                "type": "response.completed",
                "response": response,
                "sequence_number": len(events),
            }
        )
        events.append(
            {
                "type": "response.usage",
                "usage": {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26},
            }
        )
    replies = [
        _stream(events),
        _responses_sse_text("Both complete")
        if api_mode == API_MODE_RESPONSES
        else _chat_sse_text("Both complete"),
    ]
    requests = []

    async def send(request, *args, **kwargs):
        body = json.loads(request.content)
        _assert_valid_outgoing_history(body, api_mode)
        requests.append(body)
        assert len(requests) <= 2
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=replies[len(requests) - 1],
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    result = await conversation.async_converse(
        hass=hass,
        text="protocol actions",
        conversation_id=None,
        context=Context(),
        language="en",
        agent_id=agent.entity_id,
    )
    assert _speech(result) == "Both complete"
    order = [0, 1] if api_mode == API_MODE_CHAT_COMPLETIONS else [1, 0]
    assert effects == [
        ("light", "turn_off", {"entity_id": [targets[index]]}) for index in order
    ]
    assert len(requests) == 2
    history = requests[1][
        "messages" if api_mode == API_MODE_CHAT_COMPLETIONS else "input"
    ]
    observed = {}
    for item in history:
        for call in item.get("tool_calls", []):
            observed[call["id"]] = json.loads(call["function"]["arguments"])
        if item.get("type") == "function_call":
            observed[item["call_id"]] = json.loads(item["arguments"])
    assert observed == dict(calls)
    assert agent._usage.runs[-1].input_tokens >= 17
