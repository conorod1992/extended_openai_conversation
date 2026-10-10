"""Dependent conversation journeys: multiple features in one surviving HA session.

Use genuine HA Assist and provider SDK request handling; do not reset the agent
between operations or inspect coverage as a proxy for actual behaviour.
"""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONVERSATION_CONTINUITY_DEVICE,
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.setup import async_setup_component
from tests_real_ha.test_cross_feature_acceptance import (
    _agent,
    _provider,
    _rule,
    _say,
    _speech,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _install_wire,
)


def _call(name: str, call_id: str, arguments: dict) -> dict:
    return {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _local(phrase, actions, response, *, continued=False, conditions=None):
    rule = _rule(
        "local_action",
        {
            "actions": actions,
            "success_response": response,
            "failure_response": "Action failed.",
        },
        phrase=phrase,
    )
    rule["continue_matching"] = continued
    if conditions is not None:
        rule["conditions"] = conditions
    return rule


def _record(marker):
    return {"action": "sequence_probe.record", "data": {"marker": marker}}


async def test_provider_local_provider_local_provider_preserves_device_continuity(
    hass, monkeypatch
):
    """Local detours neither replay side effects nor erase earlier provider output."""
    agent = await _agent(
        hass,
        **{CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE},
    )
    hits = []

    async def record(call):
        hits.append(call.data["marker"])

    hass.services.async_register("sequence_probe", "record", record)
    await agent._request_rules.async_create(
        _local("locally one", [_record("one")], "Local one.")
    )
    await agent._request_rules.async_create(
        _local("locally two", [_record("two")], "Local two.")
    )
    sent = _provider(
        monkeypatch, agent, ["Remote one.", "Remote two.", "Remote three."]
    )
    steps = [
        ("remote one", "Remote one."),
        ("locally one", "Local one."),
        ("remote two", "Remote two."),
        ("locally two", "Local two."),
        ("remote three", "Remote three."),
    ]
    ids = []
    for utterance, expected in steps:
        result = await _say(hass, agent, utterance, device_id="sequence-kitchen")
        assert _speech(result) == expected
        ids.append(result.conversation_id)
    assert len(set(ids)) == 1
    assert hits == ["one", "two"]
    assert len(sent) == 3
    final = json.dumps(sent[-1]["messages"])
    for utterance, _ in steps:
        assert utterance in final
    assert (
        final.index("remote one")
        < final.index("locally one")
        < final.index("remote two")
    )


@pytest.mark.parametrize(
    "scope,reset", [("request", False), ("conversation", False), ("conversation", True)]
)
async def test_temporary_route_survives_local_detour_and_reverts_for_followup(
    hass, monkeypatch, scope, reset
):
    agent = await _agent(hass, max_function_calls_per_conversation=0)
    route = _rule(
        "model_routing",
        {
            "model": "gpt-6-astra",
            "reasoning_effort": "xhigh",
            "scope": scope,
            "reset": False,
            "continue_to_ai": True,
        },
        match_type="starts_with",
        phrase="think deeply",
    )
    await agent._request_rules.async_create(route)
    await agent._request_rules.async_create(
        _local("local detour", [{"variables": {"detour": True}}], "Handled locally.")
    )
    if reset:
        await agent._request_rules.async_create(
            _rule(
                "model_routing",
                {
                    "scope": "conversation",
                    "reset": True,
                    "continue_to_ai": False,
                },
                phrase="reset route",
            )
        )
    sent = _provider(monkeypatch, agent, ["Deep reply.", "Followup reply."])
    first = await _say(hass, agent, "think deeply about the kitchen")
    assert _speech(first) == "Deep reply."
    middle = await _say(hass, agent, "local detour", first.conversation_id)
    assert _speech(middle) == "Handled locally."
    assert len(sent) == 1
    if reset:
        reset_result = await _say(hass, agent, "reset route", middle.conversation_id)
        assert reset_result.response.error_code is None
        assert len(sent) == 1
    last = await _say(hass, agent, "ordinary followup", middle.conversation_id)
    assert _speech(last) == "Followup reply."
    assert len(sent) == 2
    assert sent[0]["model"] == "gpt-6-astra"
    persistent = scope == "conversation" and not reset
    assert sent[1]["model"] == ("gpt-6-astra" if persistent else "gpt-5.6")
    assert sent[1]["reasoning_effort"] == ("xhigh" if persistent else "none")


async def test_successful_tool_then_provider_failure_does_not_repeat_on_later_turn(
    hass, monkeypatch
):
    """Real service side effect remains once across failed completion and recovery."""
    agent = await _agent(
        hass, **{CONF_FUNCTION_TOOLS: [deepcopy(DEFAULT_CONF_FUNCTION_TOOLS[0])]}
    )
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    hass.services.async_register("sequence_probe", "record", record)
    await agent._request_rules.async_create(
        _local("local recovery", [_record("local")], "Recovered locally.")
    )
    assert await async_setup_component(
        hass,
        "counter",
        {
            "counter": {
                "sequence_first": {"initial": 0},
                "sequence_second": {"initial": 0},
            }
        },
    )
    await hass.async_block_till_done()
    for entity_id in ("counter.sequence_first", "counter.sequence_second"):
        async_expose_entity(hass, "conversation", entity_id, True)

    def arguments(entity_id):
        return {
            "list": [
                {
                    "domain": "counter",
                    "service": "increment",
                    "service_data": {"entity_id": [entity_id]},
                }
            ]
        }

    args = arguments("counter.sequence_first")
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_tool_call("sequence-first", "execute_services", args),
            (
                400,
                {
                    "error": {
                        "message": "controlled downstream failure",
                        "type": "invalid_request_error",
                    }
                },
            ),
            _chat_sse_tool_call(
                "sequence-second",
                "execute_services",
                arguments("counter.sequence_second"),
            ),
            _chat_sse_text("Second action completed."),
        ],
    )
    first = await _say(hass, agent, "perform initial action")
    assert first.response.error_code is not None
    assert hass.states.get("counter.sequence_first").state == "1"
    assert hass.states.get("counter.sequence_second").state == "0"
    assert effects == []
    local = await _say(hass, agent, "local recovery", first.conversation_id)
    assert _speech(local) == "Recovered locally."
    assert effects == ["local"]
    assert hass.states.get("counter.sequence_first").state == "1"
    final = await _say(hass, agent, "perform followup action", local.conversation_id)
    assert _speech(final) == "Second action completed."
    assert effects == ["local"]
    assert hass.states.get("counter.sequence_first").state == "1"
    assert hass.states.get("counter.sequence_second").state == "1"
    assert len(wire.requests) == 4


async def test_on_demand_group_executes_again_after_intervening_conversation_turn(
    hass, monkeypatch
):
    tool = {
        "spec": {
            "name": "sequence_status",
            "description": "Return current status",
            "parameters": {"type": "object", "properties": {}},
        },
        "function": {"type": "template", "value_template": "SEQUENCE_READY"},
        "enabled": True,
    }
    agent = await _agent(
        hass,
        **{
            CONF_FUNCTION_TOOLS: [tool],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": "sequence_group",
                    "name": "Sequence",
                    "description": "Sequence status tools",
                    "loading_mode": "on_demand",
                    "functions": ["sequence_status"],
                    "enabled": True,
                }
            ],
        },
    )
    sent = _provider(
        monkeypatch,
        agent,
        [
            _call(
                "load_function_groups", "load-sequence", {"groups": ["sequence_group"]}
            ),
            _call("sequence_status", "first-status", {}),
            "First status ready.",
            "Intervening conversation.",
            _call("sequence_status", "second-status", {}),
            "Second status ready.",
        ],
    )
    first = await _say(hass, agent, "Load and check status")
    assert _speech(first) == "First status ready."
    second = await _say(hass, agent, "Talk about something else", first.conversation_id)
    assert _speech(second) == "Intervening conversation."
    third = await _say(hass, agent, "Check status again", second.conversation_id)
    assert _speech(third) == "Second status ready."
    assert len(sent) == 6
    for index, call_id in ((2, "first-status"), (5, "second-status")):
        result = next(
            item
            for item in sent[index]["messages"]
            if item.get("role") == "tool" and item.get("tool_call_id") == call_id
        )
        assert "SEQUENCE_READY" in result["content"]
    assert "sequence_status" in {
        entry["function"]["name"] for entry in sent[4]["tools"]
    }


async def test_prior_continued_rule_changes_later_condition_in_same_request(
    hass, monkeypatch
):
    """Condition observes the effect of the preceding matching local action."""
    agent = await _agent(hass)
    effects = []

    async def set_flag(call):
        hass.states.async_set("input_boolean.sequence_ready", call.data["state"])
        effects.append("set")

    async def record(call):
        effects.append("later")

    hass.services.async_register("sequence_probe", "set_flag", set_flag)
    hass.services.async_register("sequence_probe", "record", record)
    hass.states.async_set("input_boolean.sequence_ready", "off")
    first = _local(
        "run chain",
        [{"action": "sequence_probe.set_flag", "data": {"state": "on"}}],
        "First.",
        continued=True,
    )
    second = _local(
        "run chain",
        [_record("later")],
        "Second.",
        conditions=[
            {
                "condition": "state",
                "entity_id": "input_boolean.sequence_ready",
                "state": "on",
            }
        ],
    )
    # Explicit priority: the shared fixture defaults every new rule to order 0.
    second["order"] = 1
    await agent._request_rules.async_create(first)
    await agent._request_rules.async_create(second)
    sent = _provider(monkeypatch, agent, [])
    result = await _say(hass, agent, "run chain")
    assert _speech(result) == "Second."
    assert effects == ["set", "later"]
    assert hass.states.get("input_boolean.sequence_ready").state == "on"
    assert sent == []


async def test_forced_compaction_survives_local_detour_and_device_continuity(
    hass, monkeypatch
):
    """Provider-reported usage exercises production truncation, then normal turns."""
    agent = await _agent(
        hass,
        **{
            CONF_CONVERSATION_CONTINUITY: CONVERSATION_CONTINUITY_DEVICE,
            # Leave room for the live tool catalogue, which is now budgeted on
            # every round, while forcing compaction via the reported usage below.
            "context_threshold": 20000,
            "context_truncate_strategy": "clear",
        },
    )
    effects = []

    async def record(call):
        effects.append(call.data["marker"])

    hass.services.async_register("sequence_probe", "record", record)
    await agent._request_rules.async_create(
        _local("local after compact", [_record("once")], "Local retained.")
    )
    usage = {
        "id": "chat-usage",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-5.6",
        "choices": [],
        "usage": {
            "prompt_tokens": 40000,
            "completion_tokens": 1,
            "total_tokens": 40001,
        },
    }
    compacting_reply = _chat_sse_text("Recent retained.").replace(
        b"data: [DONE]", ("data: " + json.dumps(usage) + "\n\ndata: [DONE]").encode()
    )
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _chat_sse_text("Old response to remove."),
            compacting_reply,
            _chat_sse_text("Followup retained."),
        ],
    )
    steps = [
        ("old sequence context to remove", "Old response to remove."),
        ("recent sequence context to keep", "Recent retained."),
        ("local after compact", "Local retained."),
        ("followup after compact", "Followup retained."),
    ]
    ids = []
    for text, expected in steps:
        result = await _say(hass, agent, text, device_id="compaction-kitchen")
        assert _speech(result) == expected
        ids.append(result.conversation_id)
    assert len(set(ids)) == 1
    assert effects == ["once"]
    assert len(wire.requests) == 3
    final = json.dumps(wire.requests[-1]["body"]["messages"])
    assert "old sequence context to remove" not in final
    assert "Old response to remove." not in final
    for retained in (
        "recent sequence context to keep",
        "Recent retained.",
        "local after compact",
        "followup after compact",
    ):
        assert retained in final
