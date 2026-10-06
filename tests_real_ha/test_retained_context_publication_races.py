"""Retained-context publication races at the real provider boundary."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.extended_openai_conversation_responses import (
    temporary_memory as temporary_memory_module,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
)
from homeassistant.util import dt as dt_util
from tests_real_ha.test_knowledge_provider_wire_e2e import (
    _chat_sse_text as _knowledge_text,
    _chat_sse_tool_call as _knowledge_tool,
    _chat_tool_result,
    _knowledge_agent,
    _say as _knowledge_say,
)
from tests_real_ha.test_memory_provider_wire_e2e import (
    _TEMP_SCOPE,
    _USER_ID,
    _memory_agent,
    _request_text,
    _say as _memory_say,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
)

_MEMORY_A = "Publication race launch token is alpha-cobalt."
_MEMORY_B = "Publication race launch token is beta-saffron."
_TEMPORARY_MARKER = "Temporary publication marker is violet-expiry."
_KNOWLEDGE_MARKER = "knowledge-publication-secret-orchid"


async def test_memory_edit_during_retrieval_publishes_current_record(
    hass: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A search result edited before prompt publication is refreshed by ID."""
    MockUser(id=_USER_ID, name="Publication race memory user", is_owner=True).add_to_hass(
        hass
    )
    _entry, agent = await _memory_agent(hass, API_MODE_CHAT_COMPLETIONS)
    assert agent._memory is not None
    created = await agent._memory.async_add(
        _USER_ID,
        _MEMORY_A,
        "publication-race",
        "explicit",
    )
    memory_id = created["memory"]["memory_id"]

    original_search = agent._memory.async_search
    search_entered = asyncio.Event()
    release_search = asyncio.Event()
    held_once = False

    async def held_search(*args: Any, **kwargs: Any):
        nonlocal held_once
        results = await original_search(*args, **kwargs)
        if not held_once:
            held_once = True
            assert any(item.memory_id == memory_id for item in results)
            search_entered.set()
            await release_search.wait()
        return results

    monkeypatch.setattr(agent._memory, "async_search", held_search)
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Memory publication race completed.")],
    )

    turn = asyncio.create_task(
        _memory_say(
            hass,
            agent,
            "What is the publication race launch token?",
            conversation_id="memory-publication-race",
        )
    )
    await asyncio.wait_for(search_entered.wait(), 10)
    await agent._memory.async_update(
        _USER_ID,
        memory_id,
        content=_MEMORY_B,
    )
    release_search.set()

    result = await asyncio.wait_for(turn, 10)
    assert result.response.error_code is None
    assert len(wire.requests) == 1
    provider = _request_text(wire.requests[0]["body"])
    assert _MEMORY_B in provider
    assert _MEMORY_A not in provider

    stored = await agent._memory.async_search(_USER_ID, "beta-saffron")
    assert [item.content for item in stored] == [_MEMORY_B]


async def test_temporary_memory_expiring_after_prefetch_is_omitted_from_prompt(
    hass: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prefetched temporary fact is checked against the clock at publication."""
    MockUser(id=_USER_ID, name="Publication race temporary user", is_owner=True).add_to_hass(
        hass
    )
    _entry, agent = await _memory_agent(hass, API_MODE_CHAT_COMPLETIONS)
    assert agent._temporary_memory is not None

    real_now = dt_util.utcnow()
    await agent._temporary_memory.async_add(
        _TEMP_SCOPE,
        _TEMPORARY_MARKER,
        (real_now + timedelta(minutes=5)).isoformat(),
        "publication-race",
        owner_scope_id=_TEMP_SCOPE,
        source="manual",
    )

    original_retrieve = agent._async_retrieve_temporary_memories
    retrieval_entered = asyncio.Event()
    release_retrieval = asyncio.Event()
    captured: list[str] = []

    async def held_retrieve():
        records = await original_retrieve()
        captured.extend(item.content for item in records)
        retrieval_entered.set()
        await release_retrieval.wait()
        return records

    monkeypatch.setattr(agent, "_async_retrieve_temporary_memories", held_retrieve)
    wire = _install_wire(
        monkeypatch,
        agent,
        [_chat_sse_text("Temporary publication race completed.")],
    )

    turn = asyncio.create_task(
        _memory_say(
            hass,
            agent,
            "Use any current short-term context.",
            conversation_id="temporary-publication-race",
        )
    )
    await asyncio.wait_for(retrieval_entered.wait(), 10)
    assert captured == [_TEMPORARY_MARKER]

    expired_now = real_now + timedelta(minutes=10)
    monkeypatch.setattr(temporary_memory_module.dt_util, "utcnow", lambda: expired_now)
    release_retrieval.set()

    result = await asyncio.wait_for(turn, 10)
    assert result.response.error_code is None
    assert len(wire.requests) == 1
    provider = _request_text(wire.requests[0]["body"])
    assert _TEMPORARY_MARKER not in provider


async def test_deleted_knowledge_source_after_search_never_reaches_tool_result(
    hass: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source deleted after search selection is removed before tool publication."""
    agent = await _knowledge_agent(hass, API_MODE_CHAT_COMPLETIONS)
    assert agent._knowledge is not None
    source = await agent._knowledge.async_create(
        "Publication race source",
        "Reference used only for the publication-race test",
        f"The private reference marker is {_KNOWLEDGE_MARKER}.",
    )

    original_search = agent._knowledge.async_search
    search_entered = asyncio.Event()
    release_search = asyncio.Event()
    held_once = False

    async def held_search(*args: Any, **kwargs: Any):
        nonlocal held_once
        results = await original_search(*args, **kwargs)
        if not held_once:
            held_once = True
            assert any(item.source_id == source.source_id for item in results)
            assert _KNOWLEDGE_MARKER in json.dumps(
                [item.excerpt for item in results], ensure_ascii=False
            )
            search_entered.set()
            await release_search.wait()
        return results

    monkeypatch.setattr(agent._knowledge, "async_search", held_search)
    call_id = "call-knowledge-publication-race"
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _knowledge_tool(
                call_id,
                "knowledge_search",
                {"query": "knowledge publication secret orchid", "limit": 5},
            ),
            _knowledge_text("Knowledge publication race completed."),
        ],
    )

    turn = asyncio.create_task(
        _knowledge_say(hass, agent, "Find the private publication reference.")
    )
    await asyncio.wait_for(search_entered.wait(), 10)
    assert await agent._knowledge.async_delete(source.source_id) is True
    release_search.set()

    result = await asyncio.wait_for(turn, 10)
    assert result.response.error_code is None
    assert len(wire.requests) == 2

    tool_result = _chat_tool_result(wire.requests[1]["body"], call_id)
    assert tool_result["results"] == []
    serialized = json.dumps(wire.requests[1]["body"], ensure_ascii=False)
    assert _KNOWLEDGE_MARKER not in serialized
    assert source.source_id not in serialized

    assert await agent._knowledge.async_search(
        "knowledge publication secret orchid"
    ) == []
