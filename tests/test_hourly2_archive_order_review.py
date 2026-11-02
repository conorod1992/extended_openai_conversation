"""The durable Archive representation must preserve actual conversation order."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from homeassistant.util import dt as dt_util
from tests.test_conversation_archive import FakeArchiveStorage, _session, _turn


async def test_restart_preserves_turn_order_after_clock_correction_at_month_boundary(
    monkeypatch,
):
    storage = FakeArchiveStorage()
    manager = ConversationArchive(storage, "agent")
    await manager.async_initialize()
    now = datetime(2026, 11, 1, 0, 0, 5, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    session = await manager.async_begin_session(
        "conversation",
        user_scope("alice", source="authenticated_user"),
        "ha-id",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await manager.async_record_turn(
        session.session_id,
        run_id="first",
        user_text="First question",
        assistant_text="First reply",
        successful=True,
    )
    now = datetime(2026, 10, 31, 23, 59, 45, tzinfo=UTC)
    await manager.async_record_turn(
        session.session_id,
        run_id="second",
        user_text="Second question",
        assistant_text="Second reply",
        successful=True,
    )
    before = await manager.async_get("user:alice", session.session_id)
    assert [turn["run_id"] for turn in before["turns"]] == ["first", "second"]
    restarted = ConversationArchive(storage, "agent")
    await restarted.async_initialize()
    after = await restarted.async_get("user:alice", session.session_id)
    assert [turn["run_id"] for turn in after["turns"]] == ["first", "second"]


def _legacy_turns():
    return [
        _turn(turn_id="first", timestamp="2026-11-01T00:00:05+00:00"),
        _turn(turn_id="second", timestamp="2026-10-31T23:59:45+00:00"),
    ]


@pytest.mark.parametrize("staged", [False, True])
async def test_legacy_snapshot_order_survives_restore_and_cold_restart(hass, staged):
    storage = HomeAssistantArchiveStorage(hass, "entry", "agent")
    session = _session(turn_count=2)
    turns = _legacy_turns()
    if staged:
        gate = get_agent_maintenance_gate(hass, "entry", "agent")
        async with gate.exclusive():
            gate.recovery_required = True
            with gate.recovery_work():
                await storage.async_stage_journal_recovery([session], turns, {})
            gate.recovery_required = False
    else:
        manager = ConversationArchive(storage, "agent")
        await manager.async_initialize()
        await manager.async_replace_backup([session], turns)
    restarted = ConversationArchive(storage, "agent")
    await restarted.async_initialize()
    result = await restarted.async_get("user:alice", session.session_id)
    assert [turn["turn_id"] for turn in result["turns"]] == ["first", "second"]
    assert [turn["sequence"] for turn in result["turns"]] == [0, 1]
    backup = await restarted.async_backup_data()
    sessions, checked = ConversationArchive.validate_backup_data(backup, "other-agent")
    destination = ConversationArchive(FakeArchiveStorage(), "other-agent")
    await destination.async_initialize()
    await destination.async_replace_backup(sessions, list(reversed(checked)))
    assert [
        turn["turn_id"]
        for turn in (await destination.async_get("user:alice", session.session_id))[
            "turns"
        ]
    ] == ["first", "second"]


async def test_legacy_load_does_not_rewrite_then_append_upgrades_all_months_atomically(
    monkeypatch,
):
    storage = FakeArchiveStorage()
    turns = list(reversed(_legacy_turns()))
    storage.metadata = {
        "sessions": [asdict(_session(turn_count=2))],
        "active": {},
        "partitions": ["2026-10", "2026-11"],
    }
    storage.partitions = {
        turn.timestamp[:7]: {
            "turns": [
                {key: value for key, value in asdict(turn).items() if key != "sequence"}
            ]
        }
        for turn in turns
    }
    original = deepcopy((storage.metadata, storage.partitions))
    manager = ConversationArchive(storage, "agent")
    await manager.async_initialize()
    assert (storage.metadata, storage.partitions) == original
    assert storage.metadata_save_count == storage.partition_save_count == 0
    assert [
        turn["turn_id"]
        for turn in (await manager.async_get("user:alice", "session-1"))["turns"]
    ] == ["second", "first"]
    monkeypatch.setattr(
        dt_util, "utcnow", lambda: datetime(2026, 10, 31, 23, 59, 55, tzinfo=UTC)
    )
    storage.fail_partition_on = 2
    with pytest.raises(OSError, match="partition write failed"):
        await manager.async_record_turn(
            "session-1",
            run_id="new",
            user_text="Later recorded turn",
            assistant_text="Reply",
            successful=True,
        )
    pending = storage.metadata["pending_partitions"]
    assert set(pending) == {"2026-10", "2026-11"}
    assert sorted(
        turn["sequence"] for payload in pending.values() for turn in payload["turns"]
    ) == [0, 1, 2]
    storage.fail_partition_on = None
    restarted = ConversationArchive(storage, "agent")
    await restarted.async_initialize()
    result = await restarted.async_get("user:alice", "session-1")
    assert [turn["turn_id"] for turn in result["turns"][:2]] == ["second", "first"]
    assert result["turns"][2]["run_id"] == "new"
    assert "pending_partitions" not in storage.metadata


async def test_appended_sequence_uses_maximum_without_renumbering_gaps():
    storage = FakeArchiveStorage()
    manager = ConversationArchive(storage, "agent")
    await manager.async_initialize()
    turns = [
        replace(turn, sequence=sequence)
        for turn, sequence in zip(_legacy_turns(), [4, 9], strict=True)
    ]
    await manager.async_replace_backup([_session(turn_count=2)], turns)
    await manager.async_record_turn(
        "session-1",
        run_id="new",
        user_text="Next",
        assistant_text="Reply",
        successful=True,
    )
    result = await manager.async_get("user:alice", "session-1")
    assert [turn["sequence"] for turn in result["turns"]] == [4, 9, 10]


@pytest.mark.parametrize("sequences", [[True, 1], [-1, 1], ["0", 1], [0, 0], [None, 1]])
async def test_malformed_or_ambiguous_sequence_preserves_store_and_rejects_replay(
    sequences,
):
    turns = [
        {**asdict(turn), "sequence": sequence}
        for turn, sequence in zip(_legacy_turns(), sequences, strict=True)
    ]
    document = {"sessions": [asdict(_session(turn_count=2))], "turns": turns}
    with pytest.raises(ValueError):
        ConversationArchive.validate_backup_data(document, "agent")
    storage = FakeArchiveStorage()
    storage.metadata = {
        "sessions": document["sessions"],
        "active": {},
        "partitions": ["2026-10", "2026-11"],
    }
    storage.partitions = {turn["timestamp"][:7]: {"turns": [turn]} for turn in turns}
    original = deepcopy((storage.metadata, storage.partitions))
    manager = ConversationArchive(storage, "agent")
    await manager.async_initialize()
    assert manager.stats()["storage_repair_required"] is True
    with pytest.raises(ValueError, match="needs repair"):
        await manager.async_backup_data()
    assert (storage.metadata, storage.partitions) == original
    storage.metadata["pending_partitions"] = deepcopy(storage.partitions)
    pending = deepcopy((storage.metadata, storage.partitions))
    with pytest.raises(ValueError, match="transaction is corrupted"):
        await ConversationArchive(storage, "agent").async_initialize()
    assert (storage.metadata, storage.partitions) == pending
