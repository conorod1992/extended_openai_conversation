"""Cross-feature journeys through HA conversation and the real provider/tool loop."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

from openai.types.chat import ChatCompletionChunk

from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_GUEST_EXCLUDED_ENTITIES,
    CONF_GUEST_POLICY_VERSION,
    CONF_REASONING_EFFORT,
    CONVERSATION_CONTINUITY_DEVICE,
    GUEST_POLICY_VERSION,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GUEST_MODE_UNAVAILABLE,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_MATCHING,
)
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry


async def _agent(hass, **options):
    entry = _make_entry(
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: "chat_completions",
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_REASONING_EFFORT: "medium",
            CONF_FUNCTION_TOOLS: [],
            **options,
        },
    )
    await _setup_entry(hass, entry)
    return conversation.async_get_agent(hass, entry.entry_id)


async def _say(hass, agent, text, conversation_id=None, *, device_id=None):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(),
        language="en",
        agent_id=agent.entry.entry_id,
        device_id=device_id,
    )


def _speech(result):
    assert result.response.error_code is None
    return result.response.as_dict()["speech"]["plain"]["speech"]


def _rule(action_type, action, *, match_type="equals", phrase="good night"):
    return {
        "name": "Acceptance rule",
        "enabled": True,
        "phrases": [phrase],
        "match_type": match_type,
        "action_type": action_type,
        "action": action,
        "matching_behavior": "defaults",
        "matching": dict(DEFAULT_MATCHING),
        "order": 0,
    }


def _action(entity_id):
    return {
        "domain": "light",
        "service": "turn_off",
        "target": {"entity_id": [entity_id]},
        "data": {},
    }


def _provider(monkeypatch, agent, replies):
    """Script only external transport; retain HA streaming and integration dispatch."""
    sent = []

    async def create(**kwargs):
        sent.append(deepcopy(kwargs))
        assert len(sent) <= len(replies), "Unexpected extra provider request"
        reply = replies[len(sent) - 1]
        delta = (
            {"content": reply} if isinstance(reply, str) else {"tool_calls": [reply]}
        )
        chunk = ChatCompletionChunk.model_validate(
            {
                "id": f"chat-{len(sent)}",
                "created": 0,
                "model": kwargs["model"],
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": "stop"
                        if isinstance(reply, str)
                        else "tool_calls",
                    }
                ],
            }
        )

        async def stream():
            yield chunk

        return stream()

    monkeypatch.setattr(agent._client.chat.completions, "create", create)
    return sent


async def test_consumed_rule_commits_local_exchange_and_continues_to_provider(
    hass,
    monkeypatch,
):
    agent = await _agent(
        hass, **{CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE}
    )
    calls = []

    async def turn_off(call):
        calls.append(call)

    hass.services.async_register("light", "turn_off", turn_off)
    hass.states.async_set("light.bedroom", "on")
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [_action("light.bedroom")],
                "success_response": "The bedroom is ready for sleep.",
                "failure_response": "Could not prepare the bedroom.",
            },
        )
    )
    # Observe the real commit as well as HA history: same-ID history alone could
    # pass even if integration-owned continuity stopped recording local turns.
    record = AsyncMock(wraps=agent._continuity.async_record_success)
    monkeypatch.setattr(agent._continuity, "async_record_success", record)
    sent = _provider(monkeypatch, agent, ["Sleep well."])

    first = await _say(hass, agent, "good night", device_id="bedroom")
    assert _speech(first) == "The bedroom is ready for sleep."
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["light.bedroom"]
    assert sent == []
    record.assert_awaited_once()
    key, claim, history = record.call_args.args
    assert key is not None and claim is not None
    assert [(item.role, item.content) for item in history[-2:]] == [
        ("user", "good night"),
        ("assistant", "The bedroom is ready for sleep."),
    ]

    second = await _say(
        hass, agent, "What did you just do?", first.conversation_id, device_id="bedroom"
    )
    assert second.conversation_id == first.conversation_id
    assert _speech(second) == "Sleep well."
    assert len(sent) == 1
    assert [(item["role"], item["content"]) for item in sent[0]["messages"][-3:]] == [
        ("user", "good night"),
        ("assistant", "The bedroom is ready for sleep."),
        ("user", "What did you just do?"),
    ]


async def test_request_only_route_reaches_transport_without_leaking(hass, monkeypatch):
    agent = await _agent(hass)
    await agent._request_rules.async_create(
        _rule(
            "model_routing",
            {
                "model": "gpt-6-astra",
                "reasoning_effort": "xhigh",
                "scope": "request",
                "reset": False,
                "success_response": "Route selected",
            },
            match_type="starts_with",
            phrase="think deeply",
        )
    )
    sent = _provider(monkeypatch, agent, ["Considered.", "Normal answer."])

    first = await _say(hass, agent, "think deeply about this puzzle")
    assert _speech(first) == "Considered."
    assert len(sent) == 1
    assert sent[0]["model"] == "gpt-6-astra"
    assert sent[0]["reasoning_effort"] == "xhigh"
    assert sent[0]["messages"][-1]["content"] == "think deeply about this puzzle"

    second = await _say(hass, agent, "Now tell me a joke", first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert _speech(second) == "Normal answer."
    assert len(sent) == 2
    assert sent[1]["model"] == "gpt-5.6"
    assert sent[1]["reasoning_effort"] == "medium"


async def test_on_demand_group_loads_in_tool_loop_and_is_conversation_scoped(
    hass,
    monkeypatch,
):
    tool = {
        "spec": {
            "name": "bedtime_status",
            "description": "Return the deterministic bedtime status.",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "Ready for sleep"},
    }
    agent = await _agent(
        hass,
        **{
            CONF_FUNCTION_TOOLS: [tool],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": "bedtime",
                    "name": "Bedtime",
                    "description": "Check bedtime readiness",
                    "loading_mode": "on_demand",
                    "functions": ["bedtime_status"],
                    "enabled": True,
                }
            ],
        },
    )
    sent = _provider(
        monkeypatch,
        agent,
        [
            {
                "index": 0,
                "id": "load-bedtime",
                "type": "function",
                "function": {
                    "name": "load_function_groups",
                    "arguments": '{"groups":["bedtime"]}',
                },
            },
            "Loaded.",
            "Still available.",
            "Fresh conversation.",
        ],
    )

    first = await _say(hass, agent, "Prepare bedtime tools")
    assert _speech(first) == "Loaded."
    assert len(sent) == 2

    def schemas(request):
        return {
            item["function"]["name"]: item["function"]
            for item in request.get("tools", [])
        }

    initial = schemas(sent[0])
    assert "bedtime_status" not in initial
    loader = initial["load_function_groups"]
    assert loader["parameters"]["properties"]["groups"]["items"]["enum"] == ["bedtime"]
    assert "Check bedtime readiness" in loader["description"]
    assert (
        schemas(sent[1])["bedtime_status"]["parameters"] == tool["spec"]["parameters"]
    )
    results = [item for item in sent[1]["messages"] if item["role"] == "tool"]
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "load-bedtime"
    loader_result = json.loads(json.loads(results[0]["content"])["result"])
    assert loader_result["status"] == "success"
    assert loader_result["loaded"] == ["bedtime"]

    second = await _say(hass, agent, "Check again", first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert _speech(second) == "Still available."
    assert len(sent) == 3  # No second loader round needed.
    assert "bedtime_status" in schemas(sent[2])

    other = await _say(hass, agent, "Start another bedtime conversation")
    assert other.conversation_id != first.conversation_id
    assert _speech(other) == "Fresh conversation."
    assert len(sent) == 4
    assert "bedtime_status" not in schemas(sent[3])
    assert schemas(sent[3])["load_function_groups"] == loader
    assert not any(item["role"] == "tool" for item in sent[3]["messages"])


async def test_guest_rule_preflights_whole_sequence_and_returns_generic_denial(
    hass,
    monkeypatch,
):
    for entity_id in ("light.hall", "light.private_bedroom"):
        hass.states.async_set(entity_id, "on")
        async_expose_entity(hass, conversation.DOMAIN, entity_id, True)
    agent = await _agent(
        hass,
        **{
            CONF_GUEST_POLICY_VERSION: GUEST_POLICY_VERSION,
            CONF_GUEST_EXCLUDED_ENTITIES: ["light.private_bedroom"],
        },
    )
    calls = []

    async def turn_off(call):
        calls.append(call.data["entity_id"])

    hass.services.async_register("light", "turn_off", turn_off)
    await agent._request_rules.async_create(
        _rule(
            "local_action",
            {
                "actions": [_action("light.hall"), _action("light.private_bedroom")],
                "success_response": "Both lights are off.",
                "failure_response": "Private bedroom action failed.",
            },
        )
    )
    sent = _provider(monkeypatch, agent, [])

    owner = await _say(hass, agent, "good night")
    assert _speech(owner) == "Both lights are off."
    assert calls == [["light.hall"], ["light.private_bedroom"]]
    calls.clear()
    await agent._guest_mode.async_update_trusted(indefinite=True)

    guest = await _say(hass, agent, "good night")
    assert _speech(guest) == GUEST_MODE_UNAVAILABLE
    assert calls == []  # Even the permitted first action must not execute.
    assert sent == []
