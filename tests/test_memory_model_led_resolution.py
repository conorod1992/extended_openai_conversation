"""Batch 3: model-selected Memory actions still cross a deterministic safety gate.

These are controlled action tests, NOT evidence that a live model correctly
classifies every semantic case. Fixture labels remain provisional judgments.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.memory import memory_revision
from custom_components.extended_openai_conversation_responses.model_payload import (
    PERSISTENT_MEMORY_GUIDANCE,
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.memory import memory_tools
from tests.test_memory_model_safe_contract import entity, model_call, store


async def test_differently_keyed_related_fact_is_model_decision_not_backend_veto():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "upsert", content="My bicycle is blue.", category="transport",
        source="explicit", subject="bicycle", key="bike.colour"
    )
    first_snapshot = deepcopy((await memory.async_list("alice"))[0])
    saves = storage.saves
    second = await model_call(
        agent, "upsert", content="My bicycle is red.", category="transport",
        source="explicit", subject="bicycle", key="bike.paint"
    )
    assert first["status"] == second["status"] == "created"
    assert first["memory"]["memory_id"] != second["memory"]["memory_id"]
    assert storage.saves == saves + 1
    records = await memory.async_list("alice")
    assert len(records) == 2
    assert next(row for row in records if row.memory_id == first_snapshot.memory_id) == first_snapshot


async def test_independent_same_subject_predicates_create_without_overlap_veto():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "upsert", content="Oscar likes chicken.", category="pets",
        source="implicit", subject="Oscar", key="oscar.food.chicken"
    )
    second = await model_call(
        agent, "upsert", content="Oscar likes beef.", category="pets",
        source="implicit", subject="Oscar", key="oscar.food.beef"
    )
    assert first["status"] == "created"
    assert second["status"] == "created"
    assert len(await memory.async_list("alice")) == 2
    assert storage.saves == 2


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


async def test_unkeyed_exact_duplicate_is_noop_without_save():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "add", content="The living room light is dim.",
        category="devices", source="implicit", subject="living room lamp"
    )
    before, saves = deepcopy(storage.data), storage.saves
    again = await model_call(
        agent, "upsert", content="The living room light is dim!!!",
        category="devices", source="implicit", subject="living room lamp"
    )
    assert first["status"] == "created"
    assert again["status"] == "unchanged"
    assert again["memory"]["memory_id"] == first["memory"]["memory_id"]
    assert storage.data == before and storage.saves == saves


async def test_exact_duplicate_new_key_conflicts_without_mutating_existing_key():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "upsert", content="Milo is a collie.", category="pets",
        source="implicit", subject="Milo", key="animal.milo.breed"
    )
    before, saves = deepcopy(storage.data), storage.saves
    duplicate = await model_call(
        agent, "upsert", content="Milo is a collie.", category="pets",
        source="implicit", subject="Milo", key="pet.milo.kind"
    )
    assert duplicate["status"] == "needs_resolution"
    assert duplicate["reason"] == "exact_duplicate"
    assert duplicate["applied"] is False
    assert duplicate["candidate"]["memory_id"] == first["memory"]["memory_id"]
    assert duplicate["candidate"]["key"] == "animal.milo.breed"
    assert storage.data == before and storage.saves == saves


async def test_identical_text_may_describe_two_explicitly_distinct_subjects():
    memory, storage = await store()
    agent = entity(memory)
    for subject, key in (
        ("Kitchen", "kitchen.lamp.state"),
        ("Bedroom", "bedroom.lamp.state"),
    ):
        result = await model_call(
            agent, "upsert", content="The lamp is on.", category="devices",
            source="implicit", subject=subject, key=key,
        )
        assert result["status"] == "created"
    assert storage.saves == 2
    assert len(await memory.async_list("alice")) == 2


async def test_identical_text_with_distinct_known_valid_from_can_coexist():
    memory, storage = await store()
    agent = entity(memory)
    for year, key in (("2024", "residence.2024"), ("2026", "residence.2026")):
        result = await model_call(
            agent, "upsert", content="I live in Cork.", category="residence",
            source="implicit", key=key,
            valid_from=f"{year}-01-01T00:00:00+00:00",
        )
        assert result["status"] == "created"
    assert storage.saves == 2
    assert len(await memory.async_list("alice")) == 2


async def test_exact_candidates_are_case_sensitive_for_paths():
    memory, _storage = await store()
    agent = entity(memory)
    for path, key in (
        ("/Data/Main.py", "project.main"),
        ("/data/main.py", "test.main"),
    ):
        result = await model_call(
            agent, "upsert",
            content=f"Project path is {path}.", category="tech",
            source="implicit", subject="project path", key=key,
        )
        assert result["status"] == "created"
    assert len(await memory.async_list("alice")) == 2


async def test_multiple_identical_candidates_refuse_ambiguous_noop():
    memory, storage = await store()
    # Distinct keyed records can already exist through authenticated management.
    await memory.async_add(
        "alice", "The lamp is on.", "devices", "implicit",
        subject="Lamp", key="lamp.first",
    )
    await memory.async_add(
        "alice", "The lamp is on.", "devices", "implicit",
        subject="Lamp", key="lamp.second",
    )
    before, saves = deepcopy(storage.data), storage.saves
    result = await model_call(
        entity(memory), "upsert", content="The lamp is on.",
        category="devices", source="implicit", subject="Lamp", key="lamp.third",
    )
    assert result["status"] == "needs_resolution"
    assert result["reason"] == "ambiguous_exact_duplicate"
    assert result["applied"] is False
    assert storage.data == before and storage.saves == saves


async def test_foreign_exact_match_cannot_block_current_owner_proposal():
    memory, storage = await store()
    await memory.async_add(
        "bob", "The kitchen lamp is on.", "devices", "implicit",
        key="kitchen.lamp",
    )
    result = await model_call(
        entity(memory), "upsert", content="The kitchen lamp is on.",
        category="devices", source="implicit", key="kitchen.lamp",
    )
    assert result["status"] == "created"
    assert len(await memory.async_list("alice")) == 1
    assert len(await memory.async_list("bob")) == 1
    assert storage.saves == 2


async def test_write_only_exact_duplicate_does_not_expose_existing_record(monkeypatch):
    memory, storage = await store()
    old = await memory.async_add(
        "alice", "The kitchen lamp is on.", "devices", "implicit",
        key="kitchen.lamp",
    )
    agent = entity(memory)
    monkeypatch.setattr(agent, "_current_readable_memory_scope_ids", lambda _ctx: [])
    before, saves = deepcopy(storage.data), storage.saves
    result = await model_call(
        agent, "upsert", content="The kitchen lamp is on.",
        category="devices", source="implicit", key="kitchen.other",
    )
    assert result == {"status": "needs_resolution"}
    assert (await memory.async_list("alice"))[0].memory_id == old["memory"]["memory_id"]
    assert storage.data == before and storage.saves == saves


async def test_exact_proposal_matching_never_uses_stemming_or_overlap(monkeypatch):
    memory, _storage = await store()
    agent = entity(memory)

    def unexpected_related_candidate(*_args):
        raise AssertionError("Model proposal admission must not use lexical overlap")

    monkeypatch.setattr(memory, "_find_related_candidate", unexpected_related_candidate)
    first = await model_call(
        agent, "upsert", content="I prefer coffee.", category="preferences",
        source="implicit", key="preference.coffee",
    )
    second = await model_call(
        agent, "upsert", content="I do not prefer coffee.", category="preferences",
        source="implicit", key="preference.coffee.negative",
    )
    assert first["status"] == second["status"] == "created"
    duplicate = await model_call(
        agent, "upsert", content="I prefer coffee!!!", category="preferences",
        source="implicit", key="preference.coffee.other",
    )
    assert duplicate["status"] == "needs_resolution"
    assert duplicate["reason"] == "exact_duplicate"


async def test_concurrent_new_keys_with_identical_text_commit_once():
    memory, storage = await store()
    agent = entity(memory)
    results = await asyncio.gather(
        model_call(
            agent, "upsert", content="Oscar likes beef.", category="pets",
            source="implicit", subject="Oscar", key="oscar.food.beef",
        ),
        model_call(
            agent, "upsert", content="Oscar likes beef.", category="pets",
            source="implicit", subject="Oscar", key="oscar.food.alternate",
        ),
    )
    assert {result["status"] for result in results} == {
        "created", "needs_resolution"
    }
    assert len(await memory.async_list("alice")) == 1
    assert storage.saves == 1


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
    compact_guidance = " ".join(PERSISTENT_MEMORY_GUIDANCE.split())
    assert "plans versus completion" in compact_guidance
    assert "A search miss does not prove novelty" in compact_guidance
