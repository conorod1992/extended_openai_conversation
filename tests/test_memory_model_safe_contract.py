"""Batch 2: model-origin Memory contract, concurrency and authority regression tests.

Legacy authenticated Management APIs retain their existing operations; these
tests exercise the *model* boundary separately from the direct manager API.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    CONF_SHARED_MEMORY_MODE,
    MEMORY_MODE_AUTOMATIC,
    MEMORY_MODE_MANUAL,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
    _ACTIVE_TURN,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
    memory_revision,
    memory_tools,
)
from custom_components.extended_openai_conversation_responses.model_payload import (
    prepare_model_function_tools,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


class Storage:
    def __init__(self):
        self.data = None
        self.saves = 0

    async def async_load(self):
        return deepcopy(self.data)

    async def async_save(self, data):
        self.data = deepcopy(data)
        self.saves += 1


async def store() -> tuple[PersistentMemory, Storage]:
    storage = Storage()
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    return memory, storage


def entity(memory, *, automatic=True, shared=SHARED_MEMORY_EXPLICIT):
    result = ExtendedOpenAIAgentEntity.__new__(ExtendedOpenAIAgentEntity)
    result.subentry = SimpleNamespace(
        data={
            CONF_MEMORY_MODE: MEMORY_MODE_AUTOMATIC if automatic else MEMORY_MODE_MANUAL,
            CONF_SHARED_MEMORY_MODE: shared,
        }
    )
    result._memory = memory
    result._effective_guest_policy = lambda: SimpleNamespace(guest_active=False)
    return result


async def model_call(agent, operation, **arguments):
    token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), scope=user_scope("alice", source="test"))
    )
    try:
        return await agent._async_execute_memory_tool(operation, arguments, None)
    finally:
        _ACTIVE_TURN.reset(token)


async def test_model_create_idempotent_noop_conflict_and_no_write_on_old_identity():
    memory, storage = await store()
    agent = entity(memory)
    first = await model_call(
        agent, "upsert", content="The kitchen target is 19 C.",
        category="heating", source="explicit", key="heating.kitchen.target"
    )
    assert first["status"] == "created"
    original_id = first["memory"]["memory_id"]
    original_revision = first["memory"]["revision"]
    assert len(original_revision) == 64
    before, saves = deepcopy(storage.data), storage.saves

    duplicate = await model_call(
        agent, "upsert", content="The kitchen target is 19 C!!!",
        category="heating", source="explicit", key="heating.kitchen.target"
    )
    assert duplicate["status"] == "unchanged"
    assert duplicate["memory"]["revision"] == original_revision
    assert storage.data == before and storage.saves == saves

    replacement = await model_call(
        agent, "upsert", content="The kitchen target is 21 C.",
        category="heating", source="explicit", key="heating.kitchen.target"
    )
    assert replacement["status"] == "needs_resolution"
    assert replacement["applied"] is False
    assert replacement["reason"] == "key_collision"
    assert replacement["candidate"]["memory_id"] == original_id
    assert replacement["candidate"]["revision"] == original_revision
    assert storage.data == before and storage.saves == saves
    assert len(await memory.async_list("alice")) == 1


async def test_model_legacy_add_alias_is_just_as_safe_as_upsert():
    memory, storage = await store()
    agent = entity(memory)
    base = await memory.async_add(
        "alice", "My dog is a collie.", "pets", "implicit", subject="Dog"
    )
    before, saves = deepcopy(storage.data), storage.saves
    candidate = await model_call(
        agent, "add", content="My dog is a collie.", category="pets",
        source="explicit", subject="Dog", key="pet.dog.breed"
    )
    assert candidate["status"] == "needs_resolution"
    assert candidate["reason"] == "unkeyed_key_promotion"
    assert candidate["candidate"]["memory_id"] == base["memory"]["memory_id"]
    assert storage.data == before and storage.saves == saves


async def test_model_write_only_caller_never_receives_candidate_data(monkeypatch):
    memory, _ = await store()
    await memory.async_add(
        "alice", "The secret project codename is saffron.", "projects", "explicit",
        key="project.codename"
    )
    agent = entity(memory)
    monkeypatch.setattr(agent, "_current_readable_memory_scope_ids", lambda _ctx: [])
    result = await model_call(
        agent, "upsert", content="The secret project codename is violet.",
        category="projects", source="explicit", key="project.codename",
    )
    assert result == {"status": "needs_resolution"}


async def test_model_search_list_and_opening_prompt_expose_opaque_revision_only():
    memory, _ = await store()
    created = await memory.async_add(
        "alice", "The living room target is 19 C.", "heating", "implicit"
    )
    agent = entity(memory)
    listed = await model_call(agent, "list", scope="personal")
    assert len(listed["memories"]) == 1
    record = listed["memories"][0]
    assert record["revision"] == memory_revision((await memory.async_list("alice"))[0])
    assert record["source"] == "implicit"
    assert "user_id" not in record
    assert record["memory_id"] == created["memory"]["memory_id"]
    searched = await model_call(agent, "search", query="living room target")
    assert searched["memories"][0]["revision"] == record["revision"]

    from custom_components.extended_openai_conversation_responses.prompt import (
        _persistent_memory_context,
    )
    context = _persistent_memory_context(await memory.async_list("alice"))
    assert record["revision"] in context
    assert '"source":"implicit"' in context or '"source": "implicit"' in context


async def test_model_update_requires_revision_and_preserves_provenance():
    memory, storage = await store()
    created = await memory.async_add("alice", "The air filter was replaced.", "home", "implicit")
    memory_id = created["memory"]["memory_id"]
    agent = entity(memory, automatic=False)
    revision = memory_revision((await memory.async_list("alice"))[0])
    original_saves = storage.saves

    with pytest.raises(ValueError, match="expected_revision|update"):
        await model_call(agent, "update", memory_id=memory_id,
                         content="The air filter was cleaned.", source="explicit")
    assert storage.saves == original_saves

    result = await model_call(
        agent, "update", memory_id=memory_id, expected_revision=revision,
        content="The air filter was cleaned.", source="explicit"
    )
    assert result["status"] == "updated"
    assert result["memory"]["source"] == "implicit"
    assert result["memory"]["revision"] != revision
    assert (await memory.async_list("alice"))[0].source == "implicit"
    with pytest.raises(ValueError, match="changed"):
        await model_call(agent, "update", memory_id=memory_id,
                         expected_revision=revision, content="Outdated correction")
    assert (await memory.async_list("alice"))[0].content == "The air filter was cleaned."


async def test_model_cannot_edit_or_delete_explicit_memory_even_with_fresh_revision():
    memory, storage = await store()
    created = await memory.async_add(
        "alice", "My preferred colour is blue.", "preferences", "explicit"
    )
    memory_id = created["memory"]["memory_id"]
    revision = memory_revision((await memory.async_list("alice"))[0])
    agent = entity(memory)
    before, saves = deepcopy(storage.data), storage.saves

    with pytest.raises(ValueError, match="authenticated management editing"):
        await model_call(agent, "update", memory_id=memory_id,
                         expected_revision=revision, content="Purple", source="explicit")
    with pytest.raises(ValueError, match="authenticated management deletion"):
        await model_call(agent, "delete", targets=[
            {"memory_id": memory_id, "expected_revision": revision}
        ])
    assert storage.data == before and storage.saves == saves

    # Management still edits an explicitly stored fact with a revision.
    changed = await memory.async_update(
        "alice", memory_id, content="My preferred colour is purple.",
        expected_revision=revision, source="explicit"
    )
    assert changed.content == "My preferred colour is purple."


async def test_model_deletion_is_all_or_nothing_for_stale_and_foreign_ids():
    memory, storage = await store()
    one = await memory.async_add("alice", "First inferred note.", "general", "implicit")
    two = await memory.async_add("alice", "Second inferred note.", "general", "implicit")
    foreign = await memory.async_add("bob", "Foreign inferred note.", "general", "implicit")
    ids = [one["memory"]["memory_id"], two["memory"]["memory_id"]]
    foreign_id = foreign["memory"]["memory_id"]
    revisions = {item.memory_id: memory_revision(item) for item in (
        await memory.async_list("alice") + await memory.async_list("bob")
    )}
    agent = entity(memory)
    before = deepcopy(storage.data)

    with pytest.raises(ValueError, match="revisions"):
        await model_call(agent, "delete", memory_ids=ids)
    with pytest.raises(ValueError, match="not found or changed"):
        await model_call(agent, "delete", targets=[
            {"memory_id": ids[0], "expected_revision": revisions[ids[0]]},
            {"memory_id": foreign_id, "expected_revision": revisions[foreign_id]},
        ])
    with pytest.raises(ValueError, match="changed"):
        await model_call(agent, "delete", targets=[
            {"memory_id": ids[0], "expected_revision": revisions[ids[0]]},
            {"memory_id": ids[1], "expected_revision": "0"*64},
        ])
    assert storage.data == before
    result = await model_call(agent, "delete", targets=[
        {"memory_id": identity, "expected_revision": revisions[identity]}
        for identity in ids
    ])
    assert result == {"status": "deleted", "deleted": 2}
    assert await memory.async_list("alice") == []
    assert len(await memory.async_list("bob")) == 1


async def test_model_source_explicit_cannot_escape_implicit_privacy_rules():
    memory, storage = await store()
    agent = entity(memory, automatic=False)
    with pytest.raises(ValueError, match="sensitive"):
        await model_call(
            agent, "upsert", content="My medical diagnosis is example condition.",
            category="health", source="explicit"
        )
    assert storage.saves == 0
    assert await memory.async_list("alice") == []


async def test_model_origin_cannot_claim_manual_shared_write_authority():
    memory, storage = await store()
    agent = entity(memory, automatic=False, shared=SHARED_MEMORY_EXPLICIT)
    with pytest.raises(ValueError, match="automatic shared memory creation is disabled"):
        await model_call(
            agent, "upsert", content="The pantry shelves are green.",
            category="household", source="explicit", scope="household",
        )
    assert storage.saves == 0


def test_model_schemas_require_atomic_target_revisions_and_keep_legacy_add_hidden():
    definitions = {entry["spec"]["name"]: entry["spec"]["parameters"]
                   for entry in memory_tools()}
    assert {"memory_id", "expected_revision"} <= set(
        definitions["memory_update"]["required"]
    )
    deletion = definitions["memory_delete"]
    assert deletion["required"] == ["targets"]
    assert {"memory_id", "expected_revision"} <= set(
        deletion["properties"]["targets"]["items"]["required"]
    )
    rendered = {entry["spec"]["name"]: entry["spec"]
                for entry in prepare_model_function_tools(memory_tools())}
    assert "memory_add" not in rendered
    assert rendered["memory_delete"]["parameters"]["required"] == ["targets"]
    assert "expected_revision" in rendered["memory_update"]["parameters"]["properties"]


async def test_legacy_management_deletion_is_still_supported():
    memory, _ = await store()
    made = await memory.async_add("alice", "Admin owned fact.", "general", "explicit")
    assert await memory.async_delete(
        "alice", [made["memory"]["memory_id"], "unknown"]
    ) == 1
    assert await memory.async_list("alice") == []
