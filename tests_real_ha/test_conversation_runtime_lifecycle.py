"""Public Assist lifecycles with real provider dispatch and deterministic clocks."""

import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    function_groups,
    request_rules,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_PROMPT,
    CONF_TEMPORARY_MEMORY,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
    MEMORY_RETRIEVAL_HYBRID,
    TEMPORARY_MEMORY_BALANCED,
)
from homeassistant.components import conversation
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from tests_real_ha.test_cross_feature_acceptance import _agent, _provider, _rule, _speech


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
            "Another fresh discussion.",
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
    memory_key = state_key.rsplit(":conversation:", 1)[0] if state_key.startswith("continuity:") else state_key
    await agent._continuity.async_set_memory_bundle(
        memory_key, {"remembered-id": "prior"}, 5
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
    assert await agent._continuity.async_get_memory_bundle(memory_key, 5) is None
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
    stale_retry = await _say(hass, agent, "Another topic", first.conversation_id)
    assert _speech(stale_retry) == "Another fresh discussion."
    assert stale_retry.conversation_id != first.conversation_id
    assert all(
        item.get("content") not in {"Prior secret", "Starting fresh."}
        for item in sent[5]["messages"]
    )
    followup_id = fresh.conversation_id if mode == CONVERSATION_CONTINUITY_HA_DEFAULT else stale_retry.conversation_id
    followup = await _say(hass, agent, "Follow up", followup_id)
    assert followup.conversation_id == followup_id
    assert _speech(followup) == "Continued."
    expected_context = "New discussion." if mode == CONVERSATION_CONTINUITY_HA_DEFAULT else "Another fresh discussion."
    assert any(item.get("content") == expected_context for item in sent[6]["messages"])


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


async def test_native_startup_and_turn_accept_legacy_numeric_settings(hass, monkeypatch):
    MockUser(id="lifecycle-owner", name="Owner").add_to_hass(hass)
    agent = await _agent(
        hass,
        **{
            CONF_USAGE_REQUEST_RETENTION_DAYS: " 30.00 ",
            CONF_USAGE_RUN_RETENTION_DAYS: "90.0",
            CONF_CONVERSATION_TIMEOUT_MINUTES: "30.0",
            CONF_ARCHIVE_ENABLED: True,
            CONF_ARCHIVE_RETENTION_DAYS: "30.0",
            CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES: "15.0",
        },
    )
    assert agent._usage.request_retention_days == 30
    assert agent._usage.run_retention_days == 90
    assert agent._archive is not None
    _provider(monkeypatch, agent, ["Ready."])
    assert _speech(await _say(hass, agent, "Hello")) == "Ready."
    sessions = await agent._archive.async_list_sessions("user:lifecycle-owner")
    assert sessions["sessions"][0]["turn_count"] == 1


async def test_captured_input_is_used_for_generation_prompt_and_hybrid_recall(hass, monkeypatch):
    MockUser(id="lifecycle-owner", name="Owner").add_to_hass(hass)
    agent = await _agent(
        hass,
        **{
            CONF_ARCHIVE_ENABLED: True,
            CONF_MEMORY_ENABLED: True,
            CONF_MEMORY_RETRIEVAL_MODE: MEMORY_RETRIEVAL_HYBRID,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: "2.0",
            CONF_PROMPT: "Effective input is {{ user_input.text }}",
        },
    )
    rule = _rule(
        "model_routing",
        {"model": "gpt-5.6", "scope": "request", "continue_to_ai": True},
        match_type="sentence_pattern",
        phrase="ask for {recipient} about {question}",
    )
    rule.update(ai_input_mode="capture", ai_input_capture="question")
    await agent._request_rules.async_create(rule)
    await agent._memory.async_add(
        "lifecycle-owner", "The sky is blue on a clear day", "general", "explicit"
    )
    embedding = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])]))
    monkeypatch.setattr(agent._client.embeddings, "create", embedding)
    sent = _provider(monkeypatch, agent, ["Blue."])
    original = "ask for Alice about why the sky is blue"
    effective = "why the sky is blue"
    assert _speech(await _say(hass, agent, original)) == "Blue."
    assert embedding.await_args.kwargs["input"] == [effective]
    messages = sent[0]["messages"]
    assert messages[-1]["content"] == effective
    assert f"Effective input is {effective}" in messages[0]["content"]
    assert all(original not in item.get("content", "") for item in messages)
    archive = await agent._archive.async_search("user:lifecycle-owner", "Alice")
    assert len(archive["results"]) == 1
