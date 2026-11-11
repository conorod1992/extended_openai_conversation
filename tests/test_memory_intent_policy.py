"""Objective admission tests; model intent is scripted, never linguistically parsed."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_MODE,
    MEMORY_MODE_OFF,
    SHARED_MEMORY_AUTOMATIC,
    SHARED_MEMORY_DISABLED,
    SHARED_MEMORY_EXPLICIT,
)
from custom_components.extended_openai_conversation_responses.memory import (
    memory_revision,
)
from custom_components.extended_openai_conversation_responses.scope import (
    shared_scope,
    unretained_scope,
    user_scope,
)
from tests.test_memory_model_safe_contract import entity, model_call, store


@pytest.mark.parametrize("operation", ["add", "upsert", "update", "delete"])
@pytest.mark.parametrize("intent", [None, "automatic", "user_request"])
@pytest.mark.parametrize("source", ["explicit", "implicit"])
@pytest.mark.parametrize(
    "automatic,shared,scope,selector,proactive,requested",
    [
        (True, SHARED_MEMORY_EXPLICIT, "personal", None, True, True),
        (False, SHARED_MEMORY_EXPLICIT, "personal", None, False, True),
        (True, SHARED_MEMORY_AUTOMATIC, "shared", None, True, True),
        (True, SHARED_MEMORY_EXPLICIT, "shared", None, False, True),
        (False, SHARED_MEMORY_AUTOMATIC, "shared", None, False, True),
        (False, SHARED_MEMORY_EXPLICIT, "shared", None, False, True),
        (True, SHARED_MEMORY_EXPLICIT, "personal", "household", False, True),
        (False, SHARED_MEMORY_EXPLICIT, "personal", "household", False, True),
        (True, SHARED_MEMORY_AUTOMATIC, "personal", "household", False, True),
        (True, SHARED_MEMORY_DISABLED, "personal", "household", False, False),
        (True, SHARED_MEMORY_EXPLICIT, "shared", "personal", False, False),
        (True, SHARED_MEMORY_AUTOMATIC, "unretained", "household", False, False),
        (True, SHARED_MEMORY_AUTOMATIC, "unretained", None, False, False),
        (True, SHARED_MEMORY_AUTOMATIC, "off", None, False, False),
    ],
)
async def test_mutation_mode_scope_matrix(
    operation, intent, source, automatic, shared, scope, selector, proactive, requested
):
    memory, storage = await store()
    agent = entity(memory, automatic=automatic, shared=shared)
    turn = {
        "personal": user_scope("alice", source="test"),
        "shared": shared_scope(source="voice"),
        "unretained": unretained_scope(),
        "off": user_scope("alice", source="test"),
    }[scope]
    if scope == "off":
        agent.subentry.data[CONF_MEMORY_MODE] = MEMORY_MODE_OFF
    owner = (
        "shared:household" if scope == "shared" or selector == "household" else "alice"
    )
    await memory.async_add(
        owner, "Oscar prefers his ball.", "pets", source, key="oscar.toy"
    )
    [original] = await memory.async_list(owner)
    args = {}
    if intent is not None:
        args["intent"] = intent
    if selector is not None:
        args["scope"] = selector
    if operation in {"add", "upsert"}:
        args.update(content="Oscar is a collie.", category="pets", source=source)
    elif operation == "update":
        # The source attempts to invert provenance; model-origin updates must ignore it.
        args.update(
            memory_id=original.memory_id,
            expected_revision=memory_revision(original),
            content="Oscar prefers his dinosaur.",
            source="implicit" if source == "explicit" else "explicit",
        )
    else:
        args["targets"] = [
            {
                "memory_id": original.memory_id,
                "expected_revision": memory_revision(original),
            }
        ]
        args["source"] = (
            "explicit"  # Even a historical/unschematized label grants nothing.
        )
    before, saves = deepcopy(storage.data), storage.saves
    allowed = requested if intent == "user_request" else proactive
    if not allowed:
        with pytest.raises((ValueError, RuntimeError)):
            await model_call(agent, operation, turn_scope=turn, **args)
        assert storage.data == before and storage.saves == saves
        assert await memory.async_list(owner) == [original]
        return
    result = await model_call(agent, operation, turn_scope=turn, **args)
    assert (
        result["status"]
        == {
            "add": "created",
            "upsert": "created",
            "update": "updated",
            "delete": "deleted",
        }[operation]
    )
    if operation == "update":
        [updated] = await memory.async_list(owner)
        assert updated.memory_id == original.memory_id
        assert updated.source == original.source
        assert updated.user_id == original.user_id
        assert updated.key == original.key
        assert memory_revision(updated) != memory_revision(original)


@pytest.mark.parametrize(
    "intent", [True, False, 1, [], {}, "explicit", "requested", None]
)
@pytest.mark.parametrize("operation", ["upsert", "update", "delete"])
async def test_malformed_intent_is_not_permission(intent, operation):
    memory, storage = await store()
    await memory.async_add("alice", "Original fact.", "general", "explicit")
    [record] = await memory.async_list("alice")
    args = {"intent": intent, "source": "explicit"}
    if operation == "upsert":
        args.update(content="Another fact.", category="general")
    elif operation == "update":
        args.update(
            memory_id=record.memory_id,
            expected_revision=memory_revision(record),
            content="Changed fact.",
        )
    else:
        args["targets"] = [
            {
                "memory_id": record.memory_id,
                "expected_revision": memory_revision(record),
            }
        ]
    before, saves = deepcopy(storage.data), storage.saves
    with pytest.raises(ValueError, match="intent must"):
        await model_call(entity(memory), operation, **args)
    assert storage.data == before and storage.saves == saves


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("intent", ["automatic", "user_request"])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_intent_never_grants_foreign_record_access(automatic, intent, operation):
    memory, storage = await store()
    await memory.async_add("bob", "Bob's private record.", "general", "explicit")
    [record] = await memory.async_list("bob")
    args = {"intent": intent, "source": "explicit"}
    if operation == "update":
        args.update(
            memory_id=record.memory_id,
            expected_revision=memory_revision(record),
            content="Stolen record.",
            target_user_id="alice",
            user_id="bob",
        )
    else:
        args["targets"] = [
            {
                "memory_id": record.memory_id,
                "expected_revision": memory_revision(record),
            }
        ]
    before, saves = deepcopy(storage.data), storage.saves
    with pytest.raises(ValueError):
        await model_call(entity(memory, automatic=automatic), operation, **args)
    assert storage.data == before and storage.saves == saves


@pytest.mark.parametrize("intent", ["automatic", "user_request"])
async def test_guest_write_permission_still_required(intent):
    memory, storage = await store()
    agent = entity(memory, shared=SHARED_MEMORY_AUTOMATIC)
    agent._effective_guest_policy = lambda: SimpleNamespace(
        guest_active=True, shared_memory_write=False, shared_memory_read=True
    )
    with pytest.raises(RuntimeError):
        await model_call(
            agent,
            "upsert",
            turn_scope=shared_scope(source="voice"),
            content="Guest fact.",
            category="general",
            source="explicit",
            intent=intent,
        )
    assert storage.saves == 0


async def test_automatic_mixed_provenance_delete_keeps_atomic_revision_gate():
    memory, storage = await store()
    for source in ("implicit", "explicit"):
        await memory.async_add("alice", f"Fact from {source}.", "general", source)
    records = await memory.async_list("alice")
    targets = [
        {"memory_id": r.memory_id, "expected_revision": memory_revision(r)}
        for r in records
    ]
    before, saves = deepcopy(storage.data), storage.saves
    with pytest.raises(ValueError, match="changed"):
        await model_call(
            entity(memory),
            "delete",
            intent="automatic",
            targets=[targets[0], {**targets[1], "expected_revision": "0" * 64}],
        )
    assert storage.data == before and storage.saves == saves
    assert await model_call(
        entity(memory), "delete", intent="automatic", targets=targets
    ) == {"status": "deleted", "deleted": 2}


async def test_lower_level_explicit_restriction_flags_keep_their_contract():
    """Restricted callers retain the old veto; authenticated Management defaults stay separate."""
    memory, storage = await store()
    for source in ("implicit", "explicit"):
        await memory.async_add("alice", f"Fact from {source}.", "general", source)
    records = await memory.async_list("alice")
    explicit = next(r for r in records if r.source == "explicit")
    before, saves = deepcopy(storage.data), storage.saves
    with pytest.raises(ValueError, match="authenticated management editing"):
        await memory.async_update(
            "alice",
            explicit.memory_id,
            "Changed.",
            expected_revision=memory_revision(explicit),
            model_origin=True,
            allow_explicit_edit=False,
        )
    with pytest.raises(ValueError, match="authenticated management deletion"):
        await memory.async_delete(
            "alice",
            [r.memory_id for r in records],
            expected_revisions={r.memory_id: memory_revision(r) for r in records},
            allow_explicit_delete=False,
        )
    assert storage.data == before and storage.saves == saves
