"""Public Assist lifecycles with real provider dispatch and deterministic clocks."""

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    function_groups,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_ENABLED,
    CONF_TEMPORARY_MEMORY,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
    TEMPORARY_MEMORY_BALANCED,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _speech


def _tool(name, arguments=None):
    return {
        "index": 0,
        "id": "call-" + name,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments or {})},
    }


async def _group_agent(hass, mode=CONVERSATION_CONTINUITY_USER):
    return await _agent(
        hass,
        **{
            CONF_CONVERSATION_CONTINUITY: mode,
            CONF_CONVERSATION_TIMEOUT_MINUTES: 5,
            CONF_TEMPORARY_MEMORY: TEMPORARY_MEMORY_BALANCED,
            CONF_ARCHIVE_ENABLED: True,
            CONF_MEMORY_ENABLED: True,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_FUNCTION_TOOLS: [
                {
                    "spec": {
                        "name": "bedtime_status",
                        "description": "Bedtime",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {"type": "template", "value_template": "Ready"},
                }
            ],
            CONF_FUNCTION_GROUPS: [
                {
                    "id": "bedtime",
                    "name": "Bedtime",
                    "description": "Bedtime",
                    "loading_mode": "on_demand",
                    "functions": ["bedtime_status"],
                    "enabled": True,
                }
            ],
        },
    )


async def _say(hass, agent, text, conversation_id=None, user_id="lifecycle-owner"):
    return await conversation.async_converse(
        hass=hass,
        text=text,
        conversation_id=conversation_id,
        context=Context(user_id=user_id),
        language="en",
        agent_id=agent.entry.entry_id,
    )


def _names(request):
    return {item["function"]["name"] for item in request.get("tools", [])}


@pytest.mark.parametrize(
    "mode", [CONVERSATION_CONTINUITY_USER, CONVERSATION_CONTINUITY_HA_DEFAULT]
)
async def test_public_start_fresh_discards_transient_state_preserves_stores_and_old_id_client(
    hass, monkeypatch, mode
):
    MockUser(id="lifecycle-owner", name="Owner").add_to_hass(hass)
    agent = await _group_agent(hass, mode)
    sent = _provider(
        monkeypatch,
        agent,
        [
            _tool("load_function_groups", {"groups": ["bedtime"]}),
            "Loaded prior secret.",
            _tool("start_fresh_conversation"),
            "Starting fresh.",
            "New discussion.",
            "Continued.",
        ],
    )
    first = await _say(hass, agent, "Prior secret")
    assert _speech(first) == "Loaded prior secret."
    state_key = next(iter(agent._function_groups_runtime._sessions))
    assert agent._function_groups_runtime._sessions[state_key].loaded_group_ids == {
        "bedtime"
    }
    agent._request_rule_runtime.set(
        state_key, {"chat_model": "gpt-5-mini", "reasoning_effort": "low"}, 5
    )
    await agent._continuity.async_set_memory_bundle(
        state_key, {"remembered-id": "prior"}, 5
    )
    await agent._temporary_memory.async_add(
        "user:lifecycle-owner",
        "Keep temporary fact",
        (dt_util.utcnow() + timedelta(hours=1)).isoformat(),
        "test",
        owner_scope_id="user:lifecycle-owner",
    )
    await agent._memory.async_add(
        "user:lifecycle-owner", "Keep persistent fact", "test", "explicit"
    )
    await agent._knowledge.async_create(
        "Keep knowledge", "Test", "Keep durable knowledge"
    )
    snapshots = [
        await store.async_backup_data()
        for store in (agent._temporary_memory, agent._memory, agent._knowledge)
    ]
    archive_ids = set(agent._archive._sessions)

    reset = await _say(hass, agent, "Start over", first.conversation_id)
    assert reset.response.error_code is None, reset.response.as_dict()["speech"][
        "plain"
    ]["speech"]
    assert _speech(reset) == "Starting fresh."
    assert "bedtime_status" in _names(sent[2])
    assert sent[2]["model"] == "gpt-5-mini"
    assert state_key not in agent._function_groups_runtime._sessions
    assert agent._request_rule_runtime.get(state_key, 5) == {}
    assert await agent._continuity.async_get_memory_bundle(state_key, 5) is None
    assert not agent._function_groups_runtime._requests
    assert not agent._request_rule_runtime._requests
    assert snapshots == [
        await store.async_backup_data()
        for store in (agent._temporary_memory, agent._memory, agent._knowledge)
    ]
    assert archive_ids <= set(agent._archive._sessions)

    fresh = await _say(hass, agent, "New topic", first.conversation_id)
    assert _speech(fresh) == "New discussion."
    assert fresh.conversation_id != first.conversation_id
    assert sent[4]["model"] == "gpt-5.6"
    assert "bedtime_status" not in _names(sent[4])
    assert not any(
        item.get("content") == "Prior secret" for item in sent[4]["messages"]
    )
    assert not any(
        item.get("content") == "Starting fresh." for item in sent[4]["messages"]
    )
    followup = await _say(hass, agent, "Follow up", fresh.conversation_id)
    assert followup.conversation_id == fresh.conversation_id
    assert _speech(followup) == "Continued."
    assert any(item.get("content") == "New discussion." for item in sent[5]["messages"])


async def test_long_successful_public_request_keeps_groups_route_and_history_until_real_inactivity(
    hass, monkeypatch
):
    MockUser(id="lifecycle-owner", name="Owner").add_to_hass(hass)
    agent = await _group_agent(hass)
    now = 0.0
    wall = dt_util.utcnow()
    monkeypatch.setattr(function_groups, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(request_rules, "monotonic", lambda: now)
    monkeypatch.setattr(dt_util, "utcnow", lambda: wall)
    sent = _provider(
        monkeypatch,
        agent,
        [
            _tool("load_function_groups", {"groups": ["bedtime"]}),
            "Loaded.",
            "Long request completed.",
            "Retained.",
            "Expired.",
        ],
    )
    first = await _say(hass, agent, "Load tools")
    state_key = next(iter(agent._function_groups_runtime._sessions))
    agent._request_rule_runtime.set(
        state_key, {"chat_model": "gpt-5-mini", "reasoning_effort": "low"}, 5
    )
    entered, release = asyncio.Event(), asyncio.Event()
    create = agent._client.chat.completions.create

    async def held_create(**kwargs):
        entered.set()
        await release.wait()
        return await create(**kwargs)

    monkeypatch.setattr(agent._client.chat.completions, "create", held_create)
    task = asyncio.create_task(_say(hass, agent, "Long request", first.conversation_id))
    await asyncio.wait_for(entered.wait(), 3)
    now = 360.0
    wall += timedelta(minutes=6)
    agent._function_groups_runtime.begin("other", 5)
    agent._request_rule_runtime.get("other", 5)
    assert agent._function_groups_runtime._sessions[state_key].loaded_group_ids == {
        "bedtime"
    }
    assert state_key in agent._request_rule_runtime._conversation_overrides
    release.set()
    assert _speech(await asyncio.wait_for(task, 3)) == "Long request completed."
    monkeypatch.setattr(agent._client.chat.completions, "create", create)
    now += 1
    wall += timedelta(seconds=1)
    next_turn = await _say(hass, agent, "Continue", first.conversation_id)
    assert next_turn.conversation_id == first.conversation_id
    assert _speech(next_turn) == "Retained."
    assert sent[3]["model"] == "gpt-5-mini"
    assert "bedtime_status" in _names(sent[3])
    assert any(
        item.get("content") == "Long request completed." for item in sent[3]["messages"]
    )
    now += 301
    wall += timedelta(seconds=301)
    expired = await _say(hass, agent, "After inactivity", first.conversation_id)
    assert _speech(expired) == "Expired."
    assert expired.conversation_id != first.conversation_id
    assert sent[4]["model"] == "gpt-5.6"
    assert "bedtime_status" not in _names(sent[4])
    assert not any(
        item.get("content") == "Long request completed." for item in sent[4]["messages"]
    )
