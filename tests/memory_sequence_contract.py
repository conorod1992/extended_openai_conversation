"""Seeded state-machine oracle shared by PR and real-storage nightly tests."""

import random

from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)


async def run_memory_sequence(storage_factory, *, seed, steps, trace):
    rng = random.Random(seed)
    oracle = {}
    memory = PersistentMemory(storage_factory())
    await memory.async_initialize()
    # Seed multiple owners/keys so pagination and privacy are exercised even if
    # the random trace happens to contain mostly reloads.
    for user in ("user:alice", "user:bob"):
        for index in range(4):
            key = f"preference.drink{index}"
            content = f"Tea preference marker {index}"
            result = await memory.async_upsert(
                user, content, "preference", "implicit", key=key
            )
            oracle[user, key] = (content, "implicit", result["memory"]["memory_id"])
    trace.append({"operation": "seed", "seed": seed, "steps": steps})
    try:
        for step in range(steps):
            user = rng.choice(("user:alice", "user:bob"))
            key = f"preference.drink{rng.randrange(4)}"
            operation = rng.choice(
                (
                    "confirm",
                    "infer_same",
                    "infer_change",
                    "explicit_change",
                    "reload",
                    "search",
                )
            )
            trace.append(
                {"step": step, "operation": operation, "user": user, "key": key}
            )
            content, source, memory_id = oracle[user, key]
            if operation == "reload":
                memory = PersistentMemory(storage_factory())
                await memory.async_initialize()
            elif operation != "search":
                incoming_source = (
                    "explicit"
                    if operation in ("confirm", "explicit_change")
                    else "implicit"
                )
                incoming_content = (
                    content
                    if operation in ("confirm", "infer_same")
                    else f"Tea preference marker revised {step}"
                )
                result = await memory.async_upsert(
                    user, incoming_content, "preference", incoming_source, key=key
                )
                if source == "explicit" and incoming_source == "implicit":
                    assert result["status"] == (
                        "unchanged"
                        if content == incoming_content
                        else "needs_resolution"
                    )
                else:
                    oracle[user, key] = (incoming_content, incoming_source, memory_id)
            for owner in ("user:alice", "user:bob"):
                records = await memory.async_list(owner)
                actual = {
                    record.key: (record.content, record.source, record.memory_id)
                    for record in records
                }
                assert actual == {
                    identity[1]: value
                    for identity, value in oracle.items()
                    if identity[0] == owner
                }
                assert all(record.user_id == owner for record in records)
                ranked = await memory.async_search(owner, "tea", limit=20)
                pages = []
                for offset in range(0, len(ranked), 2):
                    pages.extend(
                        await memory.async_search(owner, "tea", limit=2, offset=offset)
                    )
                assert pages == ranked
                assert len({record.memory_id for record in pages}) == len(ranked)
        # Final cold manager must see the same durable provenance and content.
        memory = PersistentMemory(storage_factory())
        await memory.async_initialize()
        for owner in ("user:alice", "user:bob"):
            assert {
                r.key: (r.content, r.source, r.memory_id)
                for r in await memory.async_list(owner)
            } == {
                identity[1]: value
                for identity, value in oracle.items()
                if identity[0] == owner
            }
    except BaseException as error:
        error.add_note(f"Replay seed={seed} steps={steps}; operations={trace!r}")
        raise
