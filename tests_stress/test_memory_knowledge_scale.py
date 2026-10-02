"""Durable user isolation and Knowledge scale across a real HA reload."""

from __future__ import annotations

import asyncio
import base64
import json
import struct

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.extended_openai_conversation_responses.agent_configuration import (
    sync_memory_embedding_provider,
)
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    CONF_API_MODE,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_EMBEDDING_MODEL,
    CONF_MEMORY_MODE,
    CONF_MEMORY_RETRIEVAL_MODE,
    CONF_SKIP_AUTHENTICATION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    MEMORY_MODE_MANUAL,
    MEMORY_RETRIEVAL_HYBRID,
)
from custom_components.extended_openai_conversation_responses.knowledge import (
    async_get_knowledge,
)
from custom_components.extended_openai_conversation_responses.live_subentry_updates import (
    update_live_subentry,
)
from custom_components.extended_openai_conversation_responses.memory import (
    async_get_memory,
)
from homeassistant.components import conversation
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_memory_provider_wire_e2e import _USER_ID, _say
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _install_wire,
    _raw_client,
    _ScriptedWire,
    _speech,
)
from tests_stress.conftest import record


def _embedding_reply(vectors, indices=None, *, encoding="base64"):
    entries = []
    for position, vector in enumerate(vectors):
        entry = {
            "object": "embedding",
            "embedding": (
                base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()
                if encoding == "base64"
                else vector
            ),
        }
        index = position if indices is None else indices[position]
        if index != "missing":
            entry["index"] = index
        entries.append(entry)
    return 200, {
        "object": "list",
        "data": entries,
        "model": "wire-model",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


async def _hybrid_agent(hass):
    MockUser(id=_USER_ID, name="Hybrid wire user", is_owner=True).add_to_hass(hass)
    entry = _make_entry(
        "Hybrid embedding boundaries",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: API_MODE_CHAT_COMPLETIONS,
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_MEMORY_RETRIEVAL_MODE: MEMORY_RETRIEVAL_HYBRID,
            CONF_MEMORY_EMBEDDING_MODEL: "model-a",
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 1,
        },
    )
    await _setup_entry(hass, entry)
    agent = conversation.async_get_agent(hass, entry.entry_id)
    for content in (
        "Saffron key rests inside the pantry.",
        "Violet token rests beside the window.",
    ):
        await agent._memory.async_add(_USER_ID, content, "home", "explicit")
    return entry, agent


@pytest.mark.parametrize("encoding", ["base64", "float"])
async def test_shuffled_embedding_indices_preserve_semantic_ranking_and_cache(
    hass, monkeypatch, stress_trace, encoding
):
    """Reversed SDK entries must not permanently associate vectors to the wrong fact."""
    _entry, agent = await _hybrid_agent(hass)
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            _embedding_reply([[0.0, 1.0], [1.0, 0.0]], [1, 0], encoding=encoding),
            _embedding_reply([[1.0, 0.0]], encoding=encoding),
            _embedding_reply([[1.0, 0.0]], encoding=encoding),
        ],
    )
    for _ in range(2):
        result = await agent._async_search_memories(
            [_USER_ID], "Locate the credential", 1
        )
        assert [item.content for item in result] == [
            "Saffron key rests inside the pantry."
        ]
    assert (
        len(wire.requests) == 3
    )  # Second query uses the cached document associations.
    assert len(wire.requests[0]["body"]["input"]) == 2
    assert wire.requests[0]["body"]["encoding_format"] == "base64"
    record(
        stress_trace,
        "summary",
        embedding_association_cases=1,
        embedding_cache_rechecks=1,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "missing",
        "duplicate",
        "negative",
        "range",
        "fractional",
        "boolean",
        "transient",
        "nonfinite",
    ],
)
async def test_malformed_embedding_wire_falls_back_and_recovers(
    hass, monkeypatch, stress_trace, fault
):
    """Reject association faults before any document vector can enter the cache."""
    _entry, agent = await _hybrid_agent(hass)
    indices = {
        "missing": [0, "missing"],
        "duplicate": [0, 0],
        "negative": [0, -1],
        "range": [0, 2],
        "fractional": [0, 0.5],
        "boolean": [0, True],
    }.get(fault, [0, 1])
    bad = _embedding_reply([[1.0, 0.0], [0.0, 1.0]], indices)
    if fault == "count":
        bad = _embedding_reply([[1.0, 0.0]])
    elif fault == "transient":
        bad = (
            503,
            {
                "error": {
                    "message": "Controlled transient embedding failure",
                    "type": "service_unavailable",
                }
            },
        )
    elif fault == "nonfinite":
        bad = _embedding_reply([[float("nan"), 0.0], [0.0, 1.0]])
    failure_count = _raw_client(agent).max_retries + 1 if fault == "transient" else 1
    # Exhaust the real SDK's configured retries before proving next-call recovery.
    wire = _install_wire(
        monkeypatch,
        agent,
        [
            *([bad] * failure_count),
            _embedding_reply([[0.0, 1.0], [1.0, 0.0]], [1, 0]),
            _embedding_reply([[1.0, 0.0]]),
        ],
    )
    fallback = await agent._async_search_memories([_USER_ID], "Saffron key", 1)
    assert [item.content for item in fallback] == [
        "Saffron key rests inside the pantry."
    ]
    assert agent._memory._hybrid_status["status"] == "lexical_fallback"
    assert not agent._memory._embedding_cache
    recovered = await agent._async_search_memories(
        [_USER_ID], "Locate the credential", 1
    )
    assert [item.content for item in recovered] == [
        "Saffron key rests inside the pantry."
    ]
    assert len(wire.requests) == failure_count + 2
    record(
        stress_trace,
        "summary",
        embedding_malformed_responses=1,
        embedding_healthy_recoveries=1,
    )


async def test_live_model_change_before_manager_sync_keeps_pending_provider_bound(
    hass, monkeypatch, stress_trace
):
    """Live subentry changes cannot make a model-A manager issue a model-B query."""
    entry, agent = await _hybrid_agent(hass)
    entered, release = asyncio.Event(), asyncio.Event()
    wire = _ScriptedWire(
        [_embedding_reply([[1.0, 0.0], [0.0, 1.0]]), _embedding_reply([[1.0, 0.0]])]
    )

    async def send(request, *args, **kwargs):
        if not wire.requests:
            entered.set()
            await release.wait()
        return await wire.send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(agent)._client, "send", send)
    pending = asyncio.create_task(
        agent._memory.async_prepare_hybrid([_USER_ID], "Locate the credential")
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        update_live_subentry(
            hass,
            entry,
            agent.subentry,
            data={**agent.subentry.data, CONF_MEMORY_EMBEDDING_MODEL: "model-b"},
        )
        assert agent._memory._embedding_model == "model-a"
        release.set()
        assert await pending == [1.0, 0.0]
        assert [item["body"]["model"] for item in wire.requests] == [
            "model-a",
            "model-a",
        ]
        sync_memory_embedding_provider(agent)
        later = _install_wire(
            monkeypatch,
            agent,
            [
                _embedding_reply([[0.0, 1.0], [1.0, 0.0]]),
                _embedding_reply([[0.0, 1.0]]),
            ],
        )
        assert await agent._memory.async_prepare_hybrid([_USER_ID], "healthy") == [
            0.0,
            1.0,
        ]
        assert [item["body"]["model"] for item in later.requests] == [
            "model-b",
            "model-b",
        ]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
    record(
        stress_trace,
        "summary",
        embedding_live_configuration_races=1,
        embedding_healthy_recoveries=1,
    )


@pytest.mark.parametrize("phase", ["documents", "query"])
async def test_pending_embedding_after_real_ha_reload_cannot_poison_replacement(
    hass, monkeypatch, stress_trace, phase
):
    """Old in-flight work may finish after reload, but cannot cross the model boundary."""
    entry, old = await _hybrid_agent(hass)
    manager = old._memory
    entered, release = asyncio.Event(), asyncio.Event()
    wire = _ScriptedWire(
        [_embedding_reply([[1.0, 0.0], [0.0, 1.0]]), _embedding_reply([[1.0, 0.0]])]
    )

    async def send(request, *args, **kwargs):
        if len(wire.requests) == (0 if phase == "documents" else 1):
            entered.set()
            await release.wait()
        return await wire.send(request, *args, **kwargs)

    monkeypatch.setattr(_raw_client(old)._client, "send", send)
    pending = asyncio.create_task(manager.async_prepare_hybrid([_USER_ID], "old query"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        update_live_subentry(
            hass,
            entry,
            old.subentry,
            data={**old.subentry.data, CONF_MEMORY_EMBEDDING_MODEL: "model-b"},
        )
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        replacement = conversation.async_get_agent(hass, entry.entry_id)
        assert replacement is not old
        assert replacement._memory is manager
        replacement_wire = _install_wire(
            monkeypatch,
            replacement,
            [
                _embedding_reply([[0.0, 1.0], [1.0, 0.0]]),
                _embedding_reply([[0.0, 1.0]]),
                _embedding_reply([[0.0, 1.0]]),
                _chat_sse_text("The key is in the pantry."),
            ],
        )
        assert await manager.async_prepare_hybrid([_USER_ID], "new query") == [0.0, 1.0]
        snapshot = dict(manager._embedding_cache)
        release.set()
        assert await pending is None
        assert manager._embedding_cache == snapshot
        assert all(item.model == "model-b" for item in snapshot.values())
        result = await _say(hass, replacement, "Locate the credential")
        assert _speech(result) == "The key is in the pantry."
        body = json.dumps(replacement_wire.requests[-1]["body"])
        assert "Saffron key" in body and "Violet token" not in body
        assert all(
            item["body"]["model"] == "model-b"
            for item in replacement_wire.requests[:-1]
        )
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
    record(
        stress_trace,
        "summary",
        public_turns=1,
        embedding_reload_races=1,
        embedding_healthy_recoveries=1,
    )


@pytest.mark.asyncio
async def test_scaled_private_records_survive_reload_without_cross_user_reads(
    hass: HomeAssistant,
    stress_scale: int,
    stress_trace: list[dict],
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Large private state",
        data={CONF_API_KEY: "sk-local", CONF_SKIP_AUTHENTICATION: True},
        version=CONFIG_ENTRY_VERSION,
        subentries_data=[
            {
                "data": {},
                "subentry_type": "conversation",
                "title": "Large private agent",
                "unique_id": None,
            }
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    users = 10 * stress_scale
    per_user = 20
    sources = min(60 * stress_scale, 480)
    for user in range(users):
        for number in range(per_user):
            result = await memory.async_add(
                f"stress-user-{user}",
                f"marker-{user}-record-{number} café 東京",
                "acceptance",
                "explicit",
                key=f"stress-{user}-{number}",
            )
            assert result["status"] == "created"
    record(stress_trace, "memory_population", users=users, records=users * per_user)
    for number in range(sources):
        await knowledge.async_create(
            f"Reference {number % 7}",
            f"description {number}",
            f'Unique source {number} 🎯\n```json\n{{"value": {number}}}\n```',
            enabled=number % 5 != 0,
        )
    record(stress_trace, "knowledge_population", sources=sources)

    for user in range(users):
        records = await memory.async_list(f"stress-user-{user}", limit=50)
        assert len(records) == per_user
        assert all(
            item.user_id == f"stress-user-{user}" and f"marker-{user}-" in item.content
            for item in records
        )
    assert len(await knowledge.async_list()) == sources
    before_memory = await memory.async_backup_data()
    before_knowledge = await knowledge.async_backup_data()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    memory = await async_get_memory(hass, entry.entry_id, subentry.subentry_id)
    knowledge = await async_get_knowledge(hass, entry.entry_id, subentry.subentry_id)
    assert await memory.async_backup_data() == before_memory
    assert await knowledge.async_backup_data() == before_knowledge
    for user in range(users):
        records = await memory.async_list(f"stress-user-{user}", limit=50)
        assert len(records) == per_user
        assert all(item.user_id == f"stress-user-{user}" for item in records)
    assert len(await knowledge.async_list()) == sources
    record(
        stress_trace,
        "summary",
        users=users,
        memory_records=users * per_user,
        knowledge_sources=sources,
        reloads=1,
    )
