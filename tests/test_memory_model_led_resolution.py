"""Batch 3: model-selected Memory actions still cross a deterministic safety gate.

These are controlled action tests, NOT evidence that a live model correctly
classifies every semantic case. Fixture labels remain provisional judgments.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.memory import memory_revision
from custom_components.extended_openai_conversation_responses.model_payload import (
    PERSISTENT_MEMORY_GUIDANCE,
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from tests.test_memory_model_safe_contract import entity, model_call, store


async def test_changing_canonical_key_cannot_bypass_related_fact_conflict():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "upsert", content="My bicycle is blue.", category="transport",
        source="explicit", subject="bicycle", key="bike.colour"
    )
    before, saves = deepcopy(storage.data), storage.saves
    changed = await model_call(
        agent, "upsert", content="My bicycle is red.", category="transport",
        source="explicit", subject="bicycle", key="bike.paint"
    )
    assert first["status"] == "created"
    assert changed["status"] == "needs_resolution"
    assert changed["reason"] == "related_candidate"
    assert changed["applied"] is False
    assert changed["candidate"]["memory_id"] == first["memory"]["memory_id"]
    assert storage.data == before and storage.saves == saves
    assert len(await memory.async_list("alice")) == 1


async def test_relabelled_exact_fact_does_not_create_second_keyed_memory():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "upsert", content="Milo is a collie.", category="pets",
        source="implicit", subject="Milo", key="pet.milo.breed"
    )
    before, saves = deepcopy(storage.data), storage.saves
    second = await model_call(
        agent, "upsert", content="Milo is a collie.", category="pets",
        source="implicit", subject="Milo", key="animal.milo.kind"
    )
    assert second["status"] == "needs_resolution"
    assert second["applied"] is False
    assert first["memory"]["memory_id"] == second["candidate"]["memory_id"]
    assert storage.data == before and storage.saves == saves


async def test_new_independent_subjects_and_historical_facts_still_create():
    memory, _storage = await store()
    agent = entity(memory)
    proposals = (
        ("Milo is a collie.", "Milo", "pet.milo.breed"),
        ("Mina is a terrier.", "Mina", "pet.mina.breed"),
        ("I lived in Galway in 2024.", None, "home.history.2024"),
        ("I live in Cork in 2026.", None, "home.current.2026"),
    )
    for content, subject, key in proposals:
        metadata = {"key": key}
        if subject:
            metadata["subject"] = subject
        result = await model_call(
            agent, "upsert", content=content, category="general",
            source="implicit", **metadata
        )
        assert result["status"] == "created", (content, result)
    assert len(await memory.async_list("alice")) == len(proposals)


async def test_inferred_correction_is_targeted_revision_checked_and_durable():
    memory, storage = await store()
    agent = entity(memory)
    original = await model_call(
        agent, "upsert", content="The office target is 19 C.",
        category="heating", source="implicit", key="heating.office.target"
    )
    memory_id = original["memory"]["memory_id"]
    revision = original["memory"]["revision"]
    refused = await model_call(
        agent, "upsert", content="The office target is 21 C.",
        category="heating", source="implicit", key="heating.office.target"
    )
    assert refused["status"] == "needs_resolution"
    assert refused["applied"] is False
    assert len(await memory.async_list("alice")) == 1
    corrected = await model_call(
        agent, "update", memory_id=memory_id, expected_revision=revision,
        content="The office target is 21 C."
    )
    assert corrected["status"] == "updated"
    assert corrected["memory"]["memory_id"] == memory_id
    assert corrected["memory"]["revision"] != revision
    assert memory_revision((await memory.async_list("alice"))[0]) == corrected["memory"]["revision"]
    assert storage.data is not None
    with pytest.raises(ValueError, match="changed"):
        await model_call(
            agent, "update", memory_id=memory_id, expected_revision=revision,
            content="The office target is 23 C."
        )
    assert (await memory.async_list("alice"))[0].content == "The office target is 21 C."


def test_provider_bound_descriptions_explain_semantics_not_just_tool_names():
    tools = {
        tool["spec"]["name"]: tool["spec"]
        for tool in prepare_model_function_tools(memory_tools())
    }
    assert "memory_add" not in tools
    assert "needs_resolution" in tools["memory_upsert"]["description"]
    assert "not proof" in tools["memory_search"]["description"]
    assert "revision" in tools["memory_update"]["description"]
    assert "authenticated Management" in tools["memory_delete"]["description"]
    assert "never change it" in tools["memory_upsert"]["parameters"]["properties"]["key"]["description"]
    assert "Plans are not completed events" in PERSISTENT_MEMORY_GUIDANCE
    assert "A search miss does not prove novelty" in PERSISTENT_MEMORY_GUIDANCE
