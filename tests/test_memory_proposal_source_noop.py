"""Model source labels cannot change or conflict with unchanged stored provenance."""

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses.memory import (
    memory_as_dict,
)
from tests.test_memory_manual_duplicate_metadata import DATE, OTHER_DATE, seeded


@pytest.mark.parametrize(
    "stored,proposed", [("explicit", "implicit"), ("implicit", "explicit")]
)
@pytest.mark.parametrize("key", [None, "drink.preference"])
async def test_source_only_model_proposal_returns_authoritative_unchanged_without_write(
    stored, proposed, key
):
    memory, storage, [record] = await seeded(
        {"source": stored, "key": key, "subject": "Alice", "valid_from": DATE}
    )
    before, saves = deepcopy(storage.data), storage.save_count
    result = await memory.async_propose(
        "alice",
        record.content,
        record.category,
        proposed,
        key=key,
        subject="Alice",
        valid_from=DATE,
    )
    assert result == {
        "status": "unchanged",
        "memory": memory_as_dict(record, include_revision=True),
    }
    assert result["memory"]["source"] == stored
    assert storage.save_count == saves and storage.data == before


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "personal"},
        {"subject": None},
        {"valid_from": None},
        {"valid_from": OTHER_DATE},
        {"key": None},
        {"content": "Likes coffee."},
    ],
)
async def test_ignoring_model_source_preserves_all_other_keyed_conflicts(changes):
    memory, storage, [record] = await seeded(
        {"key": "drink.preference", "subject": "Alice", "valid_from": DATE}
    )
    before, saves = deepcopy(storage.data), storage.save_count
    values = dict(
        content=record.content,
        category=record.category,
        subject=record.subject,
        key=record.key,
        valid_from=record.valid_from,
    )
    result = await memory.async_propose(
        "alice", source="implicit", **(values | changes)
    )
    assert result["status"] == "needs_resolution" and result["applied"] is False
    assert result["candidate"] == memory_as_dict(record, include_revision=True)
    assert storage.save_count == saves and storage.data == before


async def test_unkeyed_source_only_proposal_cannot_assume_keyed_identity():
    memory, storage, _ = await seeded({"key": "drink.preference"})
    saves = storage.save_count
    result = await memory.async_propose(
        "alice", "Likes tea.", "preferences", "implicit"
    )
    assert result["status"] == "needs_resolution"
    assert storage.save_count == saves
