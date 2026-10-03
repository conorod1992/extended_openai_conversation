"""Responses execution and fairness acceptance at installation scale."""

from __future__ import annotations

import asyncio
import json
from time import monotonic

import httpx
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_MODE,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.knowledge import async_get_knowledge
from custom_components.extended_openai_conversation_responses.memory import async_get_memory
from homeassistant.components import conversation
from homeassistant.core import Context
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_provider_wire_e2e import (
    _raw_client,
    _responses_sse_text,
    _responses_sse_tool_call,
)
from tests_stress.conftest import record


def _tool(index: int) -> dict:
    return {
        "spec": {
            "name": f"scale_marker_{index}",
            "description": f"Scale marker tool {index}",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "function": {"type": "template", "value_template": f"SCALE_TOOL_RESULT_{index}"},
        "enabled": True,
    }


@pytest.mark.asyncio
async def test_populated_scale_executes_responses_retrieval_and_tool_wire(
    hass, monkeypatch, stress_scale, stress_trace
):
    owner = MockUser(id="responses-scale-owner", name="Responses scale owner", is_owner=True)
    owner.add_to_hass(hass)
    tool_count = 60 if stress_scale == 1 else 120
    memory_count = 120 if stress_scale == 1 else 240
    knowledge_count = 60 if stress_scale == 1 else 120
    tools = [_tool(index) for index in range(tool_count)]
    groups = [
        {
            "id": f"responses-scale-group-{index}",
            "name": f"Responses scale group {index}",
            "description": "Scale Responses group",
            "loading_mode": "always" if index < 10 else "on_demand",
            "functions": [f"scale_marker_{index * 3 + offset}" for offset in range(3) if index * 3 + offset < tool_count],
            "enabled": True,
        }
        for index in range(max(1, tool_count // 3))
    ]
    entry = _make_entry(
        "Responses scale execution",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_FUNCTION_TOOLS: tools,
            CONF_FUNCTION_GROUPS: groups,
        },
    )
    await _setup_entry(hass, entry)
    subentry = next(iter(entry.subentries.values()))
    agent = conversation.async_get_agent(hass, entry.entry_id)
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    for index in range(memory_count):
        await memory.async_add(
            owner.id,
            f"scale memory {index} {'RESPONSES_MEMORY_MARKER' if index == 0 else 'ordinary'}",
            "scale",
            "explicit",
            key=f"responses-scale-{index}",
        )
    for index in range(knowledge_count):
        await knowledge.async_create(
            f"Responses source {index}",
            "Scale source",
            f"scale knowledge {index} {'RESPONSES_KNOWLEDGE_MARKER' if index == 0 else 'ordinary'}",
            True,
        )

    requests = []
    replies = [
        _responses_sse_tool_call("scale-memory", "memory_search", {"query": "RESPONSES_MEMORY_MARKER", "scope": "personal", "limit": 5}),
        _responses_sse_tool_call("scale-knowledge", "knowledge_search", {"query": "RESPONSES_KNOWLEDGE_MARKER", "limit": 5}),
        _responses_sse_tool_call("scale-function", "scale_marker_0", {}),
        _responses_sse_text("Responses scale healthy"),
    ]

    async def send(request, *args, **kwargs):
        del args, kwargs
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        assert index < len(replies)
        if index == 0:
            names = {tool["name"] for tool in body["tools"] if tool.get("type") == "function"}
            assert {"memory_search", "knowledge_search", "scale_marker_0"} <= names
        if index == 1:
            assert "RESPONSES_MEMORY_MARKER" in json.dumps(body)
        if index == 2:
            assert "RESPONSES_KNOWLEDGE_MARKER" in json.dumps(body)
        if index == 3:
            assert "SCALE_TOOL_RESULT_0" in json.dumps(body)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=replies[index],
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    result = await conversation.async_converse(
        hass=hass,
        text="Use scale memory, knowledge and marker tool",
        conversation_id=None,
        context=Context(user_id=owner.id),
        language="en",
        agent_id=entry.entry_id,
    )
    assert result.response.error_code is None
    assert result.response.as_dict()["speech"]["plain"]["speech"] == "Responses scale healthy"
    assert len(requests) == 4
    record(
        stress_trace,
        "summary",
        large_responses_wire_journeys=1,
        large_responses_memory_records=memory_count,
        large_responses_knowledge_sources=knowledge_count,
        large_responses_function_tools=tool_count,
        actual_tool_executions=3,
    )


@pytest.mark.asyncio
async def test_mixed_background_pressure_preserves_lightweight_progress(
    hass, monkeypatch, stress_trace
):
    """Bounded expensive work must not starve unrelated lightweight requests."""
    owner = MockUser(id="fairness-owner", name="Fairness owner", is_owner=True)
    owner.add_to_hass(hass)
    entry = _make_entry(
        "Mixed workload fairness",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_RESPONSES,
            CONF_CHAT_MODEL: "gpt-5.6",
            "reasoning_effort": "none",
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_KNOWLEDGE_ENABLED: True,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    admitted = asyncio.Event()
    release_heavy = asyncio.Event()
    lightweight_latencies = []
    heavy_calls = 0

    async def send(request, *args, **kwargs):
        nonlocal heavy_calls
        body = json.loads(request.content)
        text = json.dumps(body)
        if "HEAVY_FAIRNESS" in text:
            heavy_calls += 1
            admitted.set()
            await release_heavy.wait()
            payload = _responses_sse_text("Heavy complete")
        else:
            payload = _responses_sse_text("Light complete")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=payload,
            request=request,
        )

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    heavy = asyncio.create_task(
        conversation.async_converse(
            hass=hass,
            text="HEAVY_FAIRNESS",
            conversation_id=None,
            context=Context(user_id=owner.id),
            language="en",
            agent_id=entry.entry_id,
        )
    )
    await asyncio.wait_for(admitted.wait(), 5)
    try:
        for index in range(8):
            started = monotonic()
            result = await asyncio.wait_for(
                conversation.async_converse(
                    hass=hass,
                    text=f"LIGHT_FAIRNESS_{index}",
                    conversation_id=None,
                    context=Context(user_id=owner.id),
                    language="en",
                    agent_id=entry.entry_id,
                ),
                5,
            )
            lightweight_latencies.append(monotonic() - started)
            assert result.response.as_dict()["speech"]["plain"]["speech"] == "Light complete"
        assert max(lightweight_latencies) < 5
        assert not heavy.done()
    finally:
        release_heavy.set()
    result = await asyncio.wait_for(heavy, 10)
    assert result.response.as_dict()["speech"]["plain"]["speech"] == "Heavy complete"
    assert heavy_calls == 1
    record(
        stress_trace,
        "summary",
        resource_fairness_cases=1,
        resource_fairness_lightweight_requests=len(lightweight_latencies),
        resource_fairness_max_light_seconds=max(lightweight_latencies),
    )
