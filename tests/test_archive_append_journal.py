"""Crash boundaries and fail-closed recovery for the Archive append journal."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from homeassistant.util import dt as dt_util
from tests.test_conversation_archive import CoverageArchiveStorage, _session, _turn

MONTH = "2026-09"
NOW = datetime(2026, 9, 16, tzinfo=UTC)


def _storage_with_month(*, existing: bool = True) -> CoverageArchiveStorage:
    storage = CoverageArchiveStorage()
    storage.metadata = {
        "sessions": [asdict(_session(turn_count=int(existing)))],
        "active": {"key": "session-1"},
        "partitions": [MONTH] if existing else [],
    }
    if existing:
        storage.partitions[MONTH] = {
            "turns": [asdict(_turn(sequence=0, user_text="previous turn"))]
        }
    return storage


async def _append(archive):
    return await archive.async_record_turn(
        "session-1",
        run_id="append-run",
        user_text="new question",
        assistant_text="new answer",
        successful=True,
    )


async def test_append_journals_one_turn_and_writes_only_its_month(monkeypatch):
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW)
    storage = _storage_with_month()
    storage.metadata["partitions"].append("2026-10")
    storage.partitions["2026-10"] = {"turns": []}
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()

    appended = await _append(archive)

    intent, committed = storage.metadata_writes
    assert intent == {
        **committed,
        "pending_append": {"turn": asdict(appended), "partition_existed": True},
    }
    assert "previous turn" not in str(intent)
    assert [month for month, _ in storage.partition_writes] == [MONTH]
    assert storage.partitions["2026-10"] == {"turns": []}
    assert [turn["sequence"] for turn in storage.partitions[MONTH]["turns"]] == [0, 1]


@pytest.mark.parametrize("existing", [False, True], ids=["new-month", "indexed-month"])
@pytest.mark.parametrize("boundary", ["intent", "partition", "cleanup"])
@pytest.mark.parametrize("saved", [False, True], ids=["before-save", "after-save"])
async def test_append_recovers_at_each_write_boundary(
    monkeypatch, existing, boundary, saved
):
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW)
    storage = _storage_with_month(existing=existing)
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()
    write_metadata = storage.async_save_metadata
    write_partition = storage.async_save_partition

    async def interrupted(write, stage, *args):
        if boundary == stage and not saved:
            raise OSError("interrupted append")
        await write(*args)
        if boundary == stage:
            raise OSError("interrupted append")

    async def save_metadata(data):
        stage = "intent" if "pending_append" in data else "cleanup"
        await interrupted(write_metadata, stage, data)

    async def save_partition(month, data):
        await interrupted(write_partition, "partition", month, data)

    with monkeypatch.context() as fault:
        fault.setattr(storage, "async_save_metadata", save_metadata)
        fault.setattr(storage, "async_save_partition", save_partition)
        with pytest.raises(OSError, match="interrupted append"):
            await _append(archive)

    intent_written = boundary != "intent" or saved
    turn_written = boundary == "cleanup" or (boundary == "partition" and saved)
    if intent_written and not (boundary == "cleanup" and saved):
        append = storage.metadata["pending_append"]
        assert append["partition_existed"] is existing
        appended_id = append["turn"]["turn_id"]
    else:
        assert "pending_append" not in storage.metadata
        appended_id = None
    prior_writes = len(storage.partition_writes)

    restarted = ConversationArchive(storage, "agent-1")
    await restarted.async_initialize()

    output = await restarted.async_get("user:alice", "session-1")
    assert [turn["user_text"] for turn in output["turns"]] == (
        (["previous turn"] if existing else [])
        + (["new question"] if intent_written else [])
    )
    if appended_id is not None:
        assert [
            turn["turn_id"]
            for turn in output["turns"]
            if turn["run_id"] == "append-run"
        ] == [appended_id]
    assert "pending_append" not in storage.metadata
    assert "pending_partitions" not in storage.metadata
    assert len(storage.partition_writes) == prior_writes + int(
        intent_written and not turn_written
    )

    again = ConversationArchive(storage, "agent-1")
    await again.async_initialize()
    assert await again.async_get("user:alice", "session-1") == output


@pytest.mark.parametrize(
    "damage",
    [
        "missing_base",
        "unreadable_base",
        "invalid_base",
        "conflicting_id",
        "duplicate_id",
        "unexpected_new_month",
        "invalid_unchanged_month",
        "bad_sequence",
        "unindexed_target",
        "ambiguous_intent",
    ],
)
async def test_append_replay_preserves_evidence_of_invalid_candidates(
    monkeypatch, damage
):
    storage = _storage_with_month()
    append = asdict(_turn(turn_id="new", sequence=1, user_text="new question"))
    storage.metadata["sessions"][0]["turn_count"] = 2
    intent = {"turn": append, "partition_existed": True}
    storage.metadata["pending_append"] = intent
    error_type = ValueError
    error_message = "transaction is corrupted"
    if damage == "missing_base":
        storage.partitions.pop(MONTH)
    elif damage == "unreadable_base":

        async def load_partition(_month):
            raise StorageRepairRequired("Conversation Archive")

        monkeypatch.setattr(storage, "async_load_partition", load_partition)
        error_message = "needs repair"
        error_type = StorageRepairRequired
    elif damage == "invalid_base":
        storage.partitions[MONTH]["turns"].append({"turn_id": "malformed"})
    elif damage == "conflicting_id":
        storage.partitions[MONTH]["turns"].append(append | {"user_text": "Other text"})
    elif damage == "duplicate_id":
        storage.partitions[MONTH]["turns"].extend([append, deepcopy(append)])
    elif damage == "unexpected_new_month":
        intent["partition_existed"] = False
    elif damage == "invalid_unchanged_month":
        storage.metadata["partitions"].append("2026-10")
        storage.partitions["2026-10"] = {"turns": [{"turn_id": "malformed"}]}
    elif damage == "bad_sequence":
        append["sequence"] = None
    elif damage == "unindexed_target":
        storage.metadata["partitions"] = []
    else:
        storage.metadata["pending_partitions"] = {}

    original = deepcopy((storage.metadata, storage.partitions))
    archive = ConversationArchive(storage, "agent-1")
    with pytest.raises(error_type, match=error_message):
        await archive.async_initialize()

    assert not archive.initialized
    assert (storage.metadata, storage.partitions) == original
    assert storage.metadata_writes == storage.partition_writes == []


async def test_append_replay_uses_cross_month_sequence_maximum_and_deduplicates(monkeypatch):
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW)
    storage = _storage_with_month()
    storage.metadata["sessions"][0]["turn_count"] = 3
    storage.partitions[MONTH]["turns"].append(
        asdict(_turn(turn_id="middle", sequence=3))
    )
    next_month = "2026-10"
    storage.metadata["partitions"].append(next_month)
    storage.partitions[next_month] = {
        "turns": [
            asdict(
                _turn(
                    turn_id="latest",
                    timestamp="2026-10-01T10:00:00+00:00",
                    sequence=7,
                )
            )
        ]
    }
    archive = ConversationArchive(storage, "agent-1")
    await archive.async_initialize()

    async def fail(*_args):
        raise OSError("interrupted append")

    with monkeypatch.context() as fault:
        fault.setattr(storage, "async_save_partition", fail)
        with pytest.raises(OSError, match="interrupted append"):
            await _append(archive)

    pending = storage.metadata["pending_append"]["turn"]
    assert pending["sequence"] == 8
    with monkeypatch.context() as fault:
        fault.setattr(storage, "async_save_metadata", fail)
        with pytest.raises(OSError, match="interrupted append"):
            await ConversationArchive(storage, "agent-1").async_initialize()

    assert [month for month, _ in storage.partition_writes] == [MONTH]
    assert storage.metadata["pending_append"]["turn"] == pending
    restarted = ConversationArchive(storage, "agent-1")
    await restarted.async_initialize()
    recovered = (await restarted.async_get("user:alice", "session-1"))["turns"]
    assert [turn["sequence"] for turn in recovered] == [0, 3, 7, 8]
    assert recovered[-1]["turn_id"] == pending["turn_id"]
    assert [month for month, _ in storage.partition_writes] == [MONTH]
    assert "pending_append" not in storage.metadata
