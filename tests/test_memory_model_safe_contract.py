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
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    user_scope,
)


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


async def model_call(
    agent, operation, *, user_text=None, turn_scope=None, **arguments
):
    """Script a tool decision; user_text labels a scenario and grants no authority."""
    token = _ACTIVE_TURN.set(
        replace(
            _ACTIVE_TURN.get(),
            scope=turn_scope or user_scope("alice", source="test"),
        )
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
        agent, "upsert", content="The kitchen target is 19 C.",
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


@pytest.mark.parametrize("suffix", ["!", "?", "."])
async def test_terminal_punctuation_requires_revision_checked_keyed_correction(suffix):
    memory, storage = await store()
    agent = entity(memory)
    values = {
        "content": "The synthetic password is Example",
        "category": "network",
        "source": "implicit",
        "key": "network.wifi.password",
    }
    first = await model_call(agent, "upsert", **values)
    original = first["memory"]
    before, saves = deepcopy(storage.data), storage.saves

    duplicate = await model_call(agent, "upsert", **values)
    assert duplicate["status"] == "unchanged"
    assert duplicate["memory"]["revision"] == original["revision"]

    changed_content = values["content"] + suffix
    conflict = await model_call(
        agent, "upsert", **{**values, "content": changed_content}
    )
    assert conflict["status"] == "needs_resolution"
    assert conflict["reason"] == "key_collision"
    assert conflict["applied"] is False
    assert conflict["candidate"]["memory_id"] == original["memory_id"]
    assert conflict["candidate"]["revision"] == original["revision"]
    assert storage.data == before and storage.saves == saves

    corrected = await model_call(
        agent, "update", memory_id=original["memory_id"],
        expected_revision=original["revision"], content=changed_content,
    )
    assert corrected["status"] == "updated"
    assert corrected["memory"]["content"] == changed_content
    assert corrected["memory"]["source"] == "implicit"
    assert corrected["memory"]["revision"] != original["revision"]
    saves = storage.saves
    repeat = await model_call(
        agent, "upsert", **{**values, "content": changed_content}
    )
    assert repeat["status"] == "unchanged"
    assert storage.saves == saves

    fresh = PersistentMemory(storage)
    await fresh.async_initialize()
    [restored] = await fresh.async_list("alice")
    assert restored.memory_id == original["memory_id"]
    assert restored.content == changed_content
    assert memory_revision(restored) == corrected["memory"]["revision"]


async def test_unkeyed_terminal_punctuation_distinctions_survive_creation_and_reload():
    memory, storage = await store()
    agent = entity(memory)
    identities = {}
    for suffix in ("", "!", "?", "."):
        content = "The synthetic password is Example" + suffix
        created = await model_call(
            agent, "upsert", content=content, category="network", source="implicit"
        )
        assert created["status"] == "created"
        identities[content] = created["memory"]["memory_id"]
    assert len(set(identities.values())) == 4

    before, saves = deepcopy(storage.data), storage.saves
    for content, identity in identities.items():
        duplicate = await model_call(
            agent, "upsert", content=content, category="network", source="implicit"
        )
        assert duplicate["status"] == "unchanged"
        assert duplicate["memory"]["memory_id"] == identity
        assert duplicate["memory"]["content"] == content
    assert storage.data == before and storage.saves == saves

    fresh = PersistentMemory(storage)
    await fresh.async_initialize()
    assert {record.content: record.memory_id for record in await fresh.async_list("alice")} == identities


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
        await model_call(agent, "update", intent="user_request", memory_id=memory_id,
                         content="The air filter was cleaned.", source="explicit")
    assert storage.saves == original_saves

    result = await model_call(
        agent, "update", intent="user_request", memory_id=memory_id, expected_revision=revision,
        content="The air filter was cleaned.", source="explicit"
    )
    assert result["status"] == "updated"
    assert result["memory"]["source"] == "implicit"
    assert result["memory"]["revision"] != revision
    assert (await memory.async_list("alice"))[0].source == "implicit"
    with pytest.raises(ValueError, match="changed"):
        await model_call(agent, "update", intent="user_request", memory_id=memory_id,
                         expected_revision=revision, content="Outdated correction")
    assert (await memory.async_list("alice"))[0].content == "The air filter was cleaned."


async def test_manual_model_update_rejects_proactive_changes_without_mutating_state():
    memory, storage = await store()
    await memory.async_add(
        "alice", "The kitchen target is 19 C.", "heating", "implicit"
    )
    original = (await memory.async_list("alice"))[0]
    before, saves = deepcopy(storage.data), storage.saves

    with pytest.raises(ValueError, match="automatic memory mutations are disabled"):
        await model_call(
            entity(memory, automatic=False),
            "update",
            memory_id=original.memory_id,
            expected_revision=memory_revision(original),
            content="The kitchen target is 21 C.",
            source="implicit",
        )

    assert await memory.async_list("alice") == [original]
    assert storage.data == before
    assert storage.saves == saves


@pytest.mark.parametrize(
    ("automatic", "source_arguments"),
    [(False, {"intent": "user_request", "source": "explicit"}), (False, {"intent": "user_request"}), (True, {"source": "implicit"})],
    ids=["manual-requested", "manual-requested-optional-source", "automatic-proactive"],
)
async def test_allowed_model_updates_preserve_inferred_provenance(
    automatic, source_arguments
):
    memory, storage = await store()
    await memory.async_add(
        "alice", "The kitchen target is 19 C.", "heating", "implicit"
    )
    original = (await memory.async_list("alice"))[0]
    saves = storage.saves

    result = await model_call(
        entity(memory, automatic=automatic),
        "update",
        memory_id=original.memory_id,
        expected_revision=memory_revision(original),
        content="The kitchen target is 21 C.",
        **source_arguments,
    )

    updated = (await memory.async_list("alice"))[0]
    assert result["status"] == "updated"
    assert updated.memory_id == original.memory_id
    assert updated.content == "The kitchen target is 21 C."
    assert updated.source == result["memory"]["source"] == "implicit"
    assert result["memory"]["revision"] == memory_revision(updated)
    assert memory_revision(updated) != memory_revision(original)
    assert storage.saves == saves + 1


async def test_manual_model_cannot_edit_or_delete_explicit_memory_without_user_request():
    memory, storage = await store()
    created = await memory.async_add(
        "alice", "My preferred colour is blue.", "preferences", "explicit"
    )
    memory_id = created["memory"]["memory_id"]
    revision = memory_revision((await memory.async_list("alice"))[0])
    agent = entity(memory, automatic=False)
    before, saves = deepcopy(storage.data), storage.saves

    with pytest.raises(ValueError, match="automatic memory mutations are disabled"):
        await model_call(agent, "update", memory_id=memory_id,
                         expected_revision=revision, content="Purple", source="explicit")
    with pytest.raises(ValueError, match="automatic memory mutations are disabled"):
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


async def test_model_source_explicit_can_store_sensitive_content_in_manual_mode():
    memory, storage = await store()
    result = await model_call(
        entity(memory, automatic=False), "upsert", intent="user_request",
        content="My medical diagnosis is example condition.",
        category="health", source="explicit",
    )
    assert result["status"] == "created"
    assert storage.saves == 1
    assert (await memory.async_list("alice"))[0].content == "My medical diagnosis is example condition."


async def test_model_origin_cannot_claim_manual_shared_write_authority():
    memory, storage = await store()
    agent = entity(memory, automatic=False, shared=SHARED_MEMORY_EXPLICIT)
    with pytest.raises(ValueError, match="automatic shared memory mutations are disabled"):
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


async def test_model_sensitive_update_preserves_provenance_with_explicit_label():
    memory, storage = await store()
    created = await memory.async_add("alice", "The household note is plain text.", "home", "implicit")
    record = (await memory.async_list("alice"))[0]
    result = await model_call(
        entity(memory), "update", memory_id=created["memory"]["memory_id"],
        expected_revision=memory_revision(record),
        content="Medical diagnosis is example condition", source="explicit",
    )
    assert result["status"] == "updated"
    [updated] = await memory.async_list("alice")
    assert updated.content == "Medical diagnosis is example condition"
    assert updated.source == "implicit"
    assert storage.saves == 2


async def test_model_multi_delete_rejects_protected_record_before_removing_anything():
    memory, storage = await store()
    inferred = await memory.async_add("alice", "Inferred record.", "general", "implicit")
    explicit = await memory.async_add("alice", "Explicit record.", "general", "explicit")
    records = await memory.async_list("alice")
    revisions = {record.memory_id: memory_revision(record) for record in records}
    before, saves = deepcopy(storage.data), storage.saves
    with pytest.raises(ValueError, match="automatic memory mutations are disabled"):
        await model_call(
            entity(memory, automatic=False),
            "delete",
            targets=[
                {"memory_id": inferred["memory"]["memory_id"],
                 "expected_revision": revisions[inferred["memory"]["memory_id"]]},
                {"memory_id": explicit["memory"]["memory_id"],
                 "expected_revision": revisions[explicit["memory"]["memory_id"]]},
            ],
        )
    assert storage.data == before
    assert storage.saves == saves
    assert len(await memory.async_list("alice")) == 2


async def test_simultaneous_model_revision_updates_only_one_wins():
    memory, _ = await store()
    created = await memory.async_add("alice", "The fan is enabled.", "home", "implicit")
    memory_id = created["memory"]["memory_id"]
    revision = memory_revision((await memory.async_list("alice"))[0])
    agent = entity(memory)
    attempted = await asyncio.gather(
        model_call(agent, "update", memory_id=memory_id,
                   expected_revision=revision, content="The fan is disabled."),
        model_call(agent, "update", memory_id=memory_id,
                   expected_revision=revision, content="The fan is automatic."),
        return_exceptions=True,
    )
    succeeded = [item for item in attempted if isinstance(item, dict)]
    rejected = [item for item in attempted if isinstance(item, ValueError)]
    assert len(succeeded) == len(rejected) == 1
    assert "changed" in str(rejected[0])
    assert (await memory.async_list("alice"))[0].content in {
        "The fan is disabled.", "The fan is automatic."
    }


async def test_model_scope_separation_does_not_allow_foreign_id_even_with_revision():
    memory, storage = await store()
    other = await memory.async_add("bob", "Other user's private fact.", "private", "implicit")
    record = (await memory.async_list("bob"))[0]
    revision = memory_revision(record)
    before, saves = deepcopy(storage.data), storage.saves
    agent = entity(memory)
    with pytest.raises(ValueError, match="memory not found"):
        await model_call(
            agent, "update", memory_id=other["memory"]["memory_id"],
            expected_revision=revision, content="An attempted change"
        )
    with pytest.raises(ValueError, match="not found or changed"):
        await model_call(agent, "delete", targets=[{
            "memory_id": other["memory"]["memory_id"],
            "expected_revision": revision,
        }])
    assert storage.data == before and storage.saves == saves


async def test_model_delete_does_not_accept_duplicate_ids_or_fake_versions():
    memory, storage = await store()
    entry = await memory.async_add("alice", "A synthetic note.", "general", "implicit")
    record = (await memory.async_list("alice"))[0]
    memory_id = entry["memory"]["memory_id"]
    revision = memory_revision(record)
    before, saves = deepcopy(storage.data), storage.saves
    agent = entity(memory)
    with pytest.raises(ValueError, match="unique"):
        await model_call(agent, "delete", targets=[
            {"memory_id": memory_id, "expected_revision": revision},
            {"memory_id": memory_id, "expected_revision": revision},
        ])
    with pytest.raises(ValueError, match="expected_revisions"):
        await model_call(agent, "delete", targets=[
            {"memory_id": memory_id, "expected_revision": "not-a-version"},
        ])
    assert storage.data == before and storage.saves == saves


async def test_direct_user_correction_and_forget_allow_explicit_record_without_promotion():
    memory, storage = await store()
    added = await memory.async_add(
        "alice", "Oscar prefers the blue ball.", "pets", "explicit"
    )
    agent = entity(memory, automatic=False)
    original = (await memory.async_list("alice"))[0]
    identity = added["memory"]["memory_id"]
    revision = memory_revision(original)
    with pytest.raises(ValueError, match="automatic memory mutations are disabled"):
        await model_call(
            agent, "update", user_text="Please change the thermostat.",
            memory_id=identity, expected_revision=revision,
            content="Oscar prefers the green dinosaur.", source="explicit",
        )
    changed = await model_call(
        agent, "update", intent="user_request",
        user_text="Actually, Oscar prefers the green dinosaur now. Update your memory.",
        memory_id=identity, expected_revision=revision,
        content="Oscar prefers the green dinosaur.",
    )
    assert changed["status"] == "updated"
    stored = (await memory.async_list("alice"))[0]
    assert stored.source == "explicit"
    assert stored.memory_id == identity
    assert stored.content == "Oscar prefers the green dinosaur."
    assert memory_revision(stored) != revision

    with pytest.raises(ValueError, match="automatic memory mutations are disabled"):
        await model_call(
            agent, "delete",
            user_text="Please remember that Oscar prefers the green dinosaur.",
            targets=[{"memory_id": identity, "expected_revision": memory_revision(stored)}],
        )
    deleted = await model_call(
        agent, "delete", intent="user_request", user_text="Please forget Oscar's favourite toy.",
        targets=[{"memory_id": identity, "expected_revision": memory_revision(stored)}],
    )
    assert deleted == {"status": "deleted", "deleted": 1}
    assert storage.saves == 3
    assert await memory.async_list("alice") == []


async def test_model_requested_sensitive_correction_preserves_explicit_provenance():
    memory, storage = await store()
    added = await memory.async_add("alice", "The kitchen is painted white.", "home", "explicit")
    record = (await memory.async_list("alice"))[0]
    result = await model_call(
        entity(memory), "update", intent="user_request", user_text="Update your memory with my medical diagnosis.",
        memory_id=added["memory"]["memory_id"], expected_revision=memory_revision(record),
        content="My medical diagnosis is example condition.", category="health", source="explicit",
    )
    assert result["status"] == "updated"
    [updated] = await memory.async_list("alice")
    assert updated.content == "My medical diagnosis is example condition."
    assert updated.source == "explicit"
    assert storage.saves == 2


@pytest.mark.parametrize("automatic", [False, True])
async def test_shared_explicit_household_create_requires_requested_intent(automatic):
    memory, storage = await store()
    agent = entity(memory, automatic=automatic, shared=SHARED_MEMORY_EXPLICIT)
    args = dict(content="The pantry shelves are green.", category="household",
                scope="household", source="explicit")
    # Neither an explicit source label nor an English command grants permission.
    with pytest.raises(ValueError, match="automatic shared memory mutations"):
        await model_call(agent, "upsert", user_text="Remember for the household.", **args)
    assert storage.saves == 0
    result = await model_call(agent, "upsert", intent="user_request", **args)
    assert result["status"] == "created"
    assert result["memory"]["source"] == "explicit"
    assert len(await memory.async_list("shared:household")) == 1
    assert await memory.async_list("alice") == []


async def test_shared_explicit_voice_accepts_direct_memory_operations_but_not_proactive():
    memory, storage = await store()
    agent = entity(memory, automatic=False, shared=SHARED_MEMORY_EXPLICIT)
    voice = shared_scope(source="test")
    created = await model_call(
        agent, "upsert", turn_scope=voice,
        intent="user_request", user_text="Remember that recycling is collected on Thursday.",
        content="Recycling is collected on Thursday.",
        category="household", source="explicit",
    )
    assert created["status"] == "created"
    saved = (await memory.async_list("shared:household"))[0]
    old_rev = memory_revision(saved)
    with pytest.raises(ValueError, match="automatic shared memory mutations are disabled"):
        await model_call(
            agent, "update", turn_scope=voice,
            memory_id=saved.memory_id, expected_revision=old_rev,
            content="Recycling is collected on Friday.",
        )
    corrected = await model_call(
        agent, "update", turn_scope=voice,
        intent="user_request", user_text="Correct your memory: recycling is collected on Friday.",
        memory_id=saved.memory_id, expected_revision=old_rev,
        content="Recycling is collected on Friday.",
    )
    assert corrected["status"] == "updated"
    current = (await memory.async_list("shared:household"))[0]
    assert current.source == "explicit"
    assert memory_revision(current) != old_rev
    with pytest.raises(ValueError, match="changed"):
        await model_call(
            agent, "delete", turn_scope=voice,
            intent="user_request", user_text="Forget the recycling collection schedule.",
            targets=[{"memory_id": saved.memory_id, "expected_revision": old_rev}],
        )
    deleted = await model_call(
        agent, "delete", turn_scope=voice,
        intent="user_request", user_text="Forget the recycling collection schedule.",
        targets=[{"memory_id": saved.memory_id, "expected_revision": memory_revision(current)}],
    )
    assert deleted == {"status": "deleted", "deleted": 1}
    assert storage.saves == 3
    assert await memory.async_list("shared:household") == []


async def test_user_requested_mixed_delete_is_atomic_with_explicit_records():
    memory, storage = await store()
    inferred = await memory.async_add("alice", "One inferred fact.", "general", "implicit")
    explicit = await memory.async_add("alice", "One explicit fact.", "general", "explicit")
    records = await memory.async_list("alice")
    by_id = {r.memory_id: memory_revision(r) for r in records}
    identities = [inferred["memory"]["memory_id"], explicit["memory"]["memory_id"]]
    agent = entity(memory)
    before, saves = deepcopy(storage.data), storage.saves
    with pytest.raises(ValueError, match="changed"):
        await model_call(
            agent, "delete", intent="user_request", user_text="Please delete these memories.",
            targets=[
                {"memory_id": identities[0], "expected_revision": by_id[identities[0]]},
                {"memory_id": identities[1], "expected_revision": "0" * 64},
            ],
        )
    assert storage.data == before and storage.saves == saves
    done = await model_call(
        agent, "delete", intent="user_request", user_text="Please delete these memories.",
        targets=[
            {"memory_id": target, "expected_revision": by_id[target]} for target in identities
        ],
    )
    assert done == {"status": "deleted", "deleted": 2}
    assert await memory.async_list("alice") == []
