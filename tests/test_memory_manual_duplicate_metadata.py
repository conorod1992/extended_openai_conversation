"""Manual duplicates must not silently discard requested metadata or identity."""

from copy import deepcopy
from dataclasses import asdict, replace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    MemoryRecord,
    PersistentMemory,
    memory_revision,
)
from tests.test_memory import FakeStorage

DATE = "2026-10-01T12:00:00+00:00"
OTHER_DATE = "2026-10-02T12:00:00+00:00"


async def seeded(*overrides):
    record = MemoryRecord(
        "first",
        "alice",
        "Likes tea.",
        "preferences",
        "explicit",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00Z",
    )
    records = [replace(record, **values) for values in (overrides or ({},))]
    storage = FakeStorage({"memories": [asdict(row) for row in records]})
    memory = PersistentMemory(storage)
    await memory.async_initialize()
    return memory, storage, records


async def test_unique_unkeyed_add_fills_date_and_persists_revision():
    memory, storage, [before] = await seeded()
    status, updated = await memory.async_add_record(
        "alice", "Likes tea.", "preferences", "explicit", valid_from=DATE
    )
    assert status == "updated"
    assert (
        updated.memory_id == before.memory_id
        and updated.created_at == before.created_at
    )
    assert updated.valid_from == DATE and updated.updated_at != before.updated_at
    assert memory_revision(updated) != memory_revision(before)
    reloaded = PersistentMemory(storage)
    await reloaded.async_initialize()
    assert await reloaded.async_list("alice") == [updated]
    saved = storage.save_count
    assert await memory.async_add_record(
        "alice", "Likes tea.", "preferences", "explicit", valid_from=DATE
    ) == ("duplicate", updated)
    assert storage.save_count == saved


@pytest.mark.parametrize(
    "existing,incoming,explanation",
    [
        ({"valid_from": DATE}, {"valid_from": OTHER_DATE}, "valid_from"),
        ({"category": "personal"}, {"valid_from": DATE}, "category"),
        ({"key": "drink.preference"}, {"valid_from": DATE}, "keyed identity"),
        ({"key": "drink.preference"}, {}, "keyed identity"),
        ({}, {"valid_from": DATE, "source": "implicit"}, "explicit-memory"),
        ({}, {"subject": "Alice", "source": "implicit"}, "explicit-memory"),
    ],
)
async def test_conflicts_are_non_mutating_and_explain_update(
    existing, incoming, explanation
):
    memory, storage, records = await seeded(existing)
    before, saved = deepcopy(storage.data), storage.save_count
    source = incoming.pop("source", "explicit")
    with pytest.raises(ValueError, match=explanation) as error:
        await memory.async_add_record(
            "alice", "Likes tea.", "preferences", source, **incoming
        )
    assert "no changes made" in str(error.value) and "Update" in str(error.value)
    assert await memory.async_list("alice") == records
    assert storage.data == before and storage.save_count == saved


@pytest.mark.parametrize(
    "subjects,requested",
    [
        ((None, None), None),
        (("Alice", None), "Alice"),
        (("Alice", "Bob"), None),
    ],
)
async def test_multiple_plausible_duplicates_never_choose_first(subjects, requested):
    memory, storage, records = await seeded(
        {"subject": subjects[0]},
        {"memory_id": "second", "subject": subjects[1], "valid_from": DATE},
    )
    before, saved = deepcopy(storage.data), storage.save_count
    with pytest.raises(ValueError, match="Multiple existing memories"):
        await memory.async_add_record(
            "alice",
            "Likes tea.",
            "preferences",
            "explicit",
            subject=requested,
            valid_from=DATE,
        )
    assert storage.data == before and storage.save_count == saved
    assert await memory.async_list("alice") == records


async def test_date_fill_rolls_back_failed_save_and_allows_intentional_update():
    memory, storage, [before] = await seeded()
    saved = deepcopy(storage.data)
    storage.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    with pytest.raises(OSError):
        await memory.async_add_record(
            "alice", "Likes tea.", "preferences", "explicit", valid_from=DATE
        )
    assert await memory.async_list("alice") == [before] and storage.data == saved
    storage.async_save = AsyncMock(
        side_effect=lambda data: setattr(storage, "data", deepcopy(data))
    )
    updated = await memory.async_update(
        "alice",
        before.memory_id,
        valid_from=OTHER_DATE,
        expected_revision=memory_revision(before),
    )
    assert updated.valid_from == OTHER_DATE


async def test_implicit_date_fill_and_manual_confirmation_preserve_provenance_rules():
    memory, _, _ = await seeded({"source": "implicit"})
    status, inferred = await memory.async_add_record(
        "alice", "Likes tea.", "preferences", "implicit", valid_from=DATE
    )
    assert status == "updated" and inferred.source == "implicit"
    status, confirmed = await memory.async_add_record(
        "alice", "Likes tea.", "preferences", "explicit", valid_from=DATE
    )
    assert status == "updated" and confirmed.source == "explicit"
    status, retained = await memory.async_add_record(
        "alice", "Likes tea.", "preferences", "implicit", valid_from=DATE
    )
    assert status == "duplicate" and retained is confirmed
