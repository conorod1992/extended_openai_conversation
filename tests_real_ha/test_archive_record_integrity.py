"""Archive repair and canonical retrieval through native Home Assistant Store I/O."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import errno
from pathlib import Path
import unicodedata

import atomicwrites
import pytest

from custom_components.extended_openai_conversation_responses.agent_maintenance import (
    get_agent_maintenance_gate,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_ARCHIVE_ENABLED,
)
from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
    HomeAssistantArchiveStorage,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    StorageRepairRequired,
)
from custom_components.extended_openai_conversation_responses.storage_recovery_diagnostics import (
    storage_recovery_snapshot,
)
from homeassistant.util import dt as dt_util
from tests.test_conversation_archive import _session, _turn
from tests_real_ha.test_cross_feature_acceptance import _agent
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
async def test_native_archive_salvages_records_repairs_counts_and_reloads_unicode(hass):
    agent = await _agent(hass, **{CONF_ARCHIVE_ENABLED: True})
    storage = agent._archive._storage
    content = unicodedata.normalize("NFD", "Café résumé belongs to Alice")
    await storage.async_save_metadata(
        {
            "sessions": [
                asdict(_session("bad")) | {"session_id": []},
                asdict(_session(turn_count=99)),
            ],
            "active": {"browser": "session-1"},
            "partitions": ["2026-09"],
        }
    )
    await storage.async_save_partition(
        "2026-09",
        {
            "turns": [
                asdict(_turn(turn_id="bad")) | {"assistant_text": {}},
                asdict(_turn(user_text=content)),
                asdict(_turn(user_text="Duplicate must not replace the original")),
            ]
        },
    )
    archive = ConversationArchive(storage, agent.subentry.subentry_id)
    await archive.async_initialize()
    assert archive.stats()["turn_count"] == 1
    assert archive.active_session("browser").turn_count == 1
    assert (await archive.async_search("user:alice", "café résumé"))["results"][0][
        "turn_id"
    ] == "turn-1"
    original = deepcopy(
        (
            await storage.async_load_metadata(),
            await storage.async_load_partition("2026-09"),
        )
    )
    with pytest.raises(ValueError, match="needs repair"):
        await archive.async_backup_data()
    with pytest.raises(ValueError, match="needs repair"):
        await archive.async_delete_session("user:alice", "session-1")
    fresh = ConversationArchive(storage, agent.subentry.subentry_id)
    await fresh.async_initialize()
    assert (await fresh.async_search("user:alice", "caf\u00e9 r\u00e9sum\u00e9"))[
        "results"
    ][0]["turn_id"] == "turn-1"
    assert (
        await storage.async_load_metadata(),
        await storage.async_load_partition("2026-09"),
    ) == original


@pytest.mark.usefixtures("real_store_io")
async def test_native_archive_quarantine_can_be_replaced_under_journal_authority(hass):
    storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    await storage.async_save_partition(
        "2026-08",
        {
            "turns": [
                asdict(
                    _turn(
                        timestamp="2026-08-01T10:00:00+00:00",
                        user_text="Unselected old content",
                    )
                )
            ]
        },
    )
    await storage.async_save_metadata({"partitions": ["2026-08"]})
    metadata = Path(storage._metadata.path)
    rejected_bytes = b"{unreadable archive metadata"
    await hass.async_add_executor_job(metadata.write_bytes, rejected_bytes)
    # Let HA itself quarantine invalid JSON before EOAI recognizes its evidence.
    with pytest.raises(StorageRepairRequired):
        await storage.async_load_metadata()

    def evidence():
        return {
            path.name: path.read_bytes()
            for path in metadata.parent.glob(metadata.name + ".corrupt.*")
        }

    rejected = await hass.async_add_executor_job(evidence)
    assert list(rejected.values()) == [rejected_bytes]
    report = await hass.async_add_executor_job(
        storage_recovery_snapshot, str(metadata.parent), "archive-entry", "archive-agent"
    )
    assert report["stores"]["archive"]["unavailable_stores"] == 1
    assert report["restore_journal"]["state"] == "absent"
    assert await hass.async_add_executor_job(evidence) == rejected
    gate = get_agent_maintenance_gate(hass, "archive-entry", "archive-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        with gate.recovery_work():
            await storage.async_stage_journal_recovery(
                [_session()], [_turn(user_text="Selected journal content")], {}
            )
        gate.recovery_required = False

    fresh_storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    fresh = ConversationArchive(fresh_storage, "archive-agent")
    await fresh.async_initialize()
    result = await fresh.async_get("user:alice", "session-1")
    assert [turn["user_text"] for turn in result["turns"]] == [
        "Selected journal content"
    ]
    assert [turn["sequence"] for turn in result["turns"]] == [0]
    assert await fresh_storage.async_load_partition("2026-08") == {"turns": []}
    assert "pending_partitions" not in await fresh_storage.async_load_metadata()
    report = await hass.async_add_executor_job(
        storage_recovery_snapshot, str(metadata.parent), "archive-entry", "archive-agent"
    )
    assert report["stores"]["archive"]["unavailable_stores"] == 0
    assert report["stores"]["archive"]["preserved_corrupt_files"] == 1
    assert await hass.async_add_executor_job(evidence) == rejected


@pytest.mark.parametrize("write", [1, 2, 3], ids=["intent", "partition", "cleanup"])
@pytest.mark.parametrize(
    "saved", [False, True], ids=["before-replace", "after-replace"]
)
@pytest.mark.usefixtures("real_store_io")
async def test_native_archive_append_recovers_at_every_atomic_replace(
    hass, monkeypatch, write, saved
):
    """Recover from actual Store disk generations, including a fully written turn."""
    month = "2026-09"
    now = datetime(2026, 9, 16, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    await storage.async_save_metadata(
        {"sessions": [asdict(_session())], "partitions": [month], "active": {}}
    )
    await storage.async_save_partition(
        month, {"turns": [asdict(_turn(sequence=0, user_text="Previous question"))]}
    )
    archive = ConversationArchive(storage, "archive-agent")
    await archive.async_initialize()

    metadata_path = Path(storage._metadata.path)
    partition_path = Path(storage._partition_store(month).path)
    atomic_replace = atomicwrites._replace_atomic
    attempts = []

    def interrupt(source, target):
        target = Path(target)
        if target not in {metadata_path, partition_path}:
            return atomic_replace(source, target)
        attempts.append(target)
        if len(attempts) != write:
            return atomic_replace(source, target)
        if saved:
            atomic_replace(source, target)
        raise OSError(errno.EIO, "Archive append interrupted at atomic replacement")

    with monkeypatch.context() as fault:
        fault.setattr(atomicwrites, "_replace_atomic", interrupt)
        with pytest.raises(OSError):
            await archive.async_record_turn(
                "session-1",
                run_id="interrupted-append",
                user_text="New question",
                assistant_text="New answer",
                successful=True,
            )
    assert attempts == [metadata_path, partition_path, metadata_path][:write]
    append_committed = write > 1 or saved
    partition_replaced = write > 2 or (write == 2 and saved)
    fresh_storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    pending = (await fresh_storage.async_load_metadata()).get("pending_append")
    if append_committed and not (write == 3 and saved):
        assert pending["turn"]["run_id"] == "interrupted-append"
    else:
        assert pending is None

    replacement_paths = []

    def observe(source, target):
        replacement_paths.append(Path(target))
        return atomic_replace(source, target)

    fresh = ConversationArchive(fresh_storage, "archive-agent")
    with monkeypatch.context() as replay:
        replay.setattr(atomicwrites, "_replace_atomic", observe)
        await fresh.async_initialize()
    recovered = await fresh.async_get("user:alice", "session-1")
    assert [(item["sequence"], item["user_text"]) for item in recovered["turns"]] == [
        (0, "Previous question")
    ] + ([(1, "New question")] if append_committed else [])
    assert replacement_paths.count(partition_path) == int(
        append_committed and not partition_replaced
    )
    assert "pending_append" not in await fresh_storage.async_load_metadata()


@pytest.mark.usefixtures("real_store_io")
async def test_full_agent_recovery_owns_a_month_from_pending_append(hass):
    storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    interrupted = _turn(sequence=0, user_text="Interrupted append")
    await storage.async_save_metadata(
        {
            "sessions": [asdict(_session())],
            "active": {},
            "partitions": ["2026-09"],
            "pending_append": {"turn": asdict(interrupted), "partition_existed": False},
        }
    )
    assert await storage.async_load_partition("2026-09") is None
    replacement = _session("replacement")
    turn = _turn(
        replacement.session_id,
        timestamp="2026-10-01T14:00:00+00:00",
        user_text="Selected replacement",
    )
    gate = get_agent_maintenance_gate(hass, "archive-entry", "archive-agent")
    async with gate.exclusive():
        gate.recovery_required = True
        with gate.recovery_work():
            await storage.async_stage_journal_recovery([replacement], [turn], {})
        gate.recovery_required = False

    intent = await storage.async_load_metadata()
    assert "pending_append" not in intent
    assert intent["pending_partitions"]["2026-09"] == {"turns": []}
    fresh = ConversationArchive(
        HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent"),
        "archive-agent",
    )
    await fresh.async_initialize()
    assert (await fresh.async_get("user:alice", replacement.session_id))["turns"][0][
        "user_text"
    ] == "Selected replacement"
    assert await storage.async_load_partition("2026-09") == {"turns": []}


@pytest.mark.parametrize("sequence", [0, 5], ids=["before-prior", "skipping-next"])
@pytest.mark.usefixtures("real_store_io")
async def test_native_append_replay_rejects_invalid_sequence_without_touching_disk(
    hass, sequence
):
    storage = HomeAssistantArchiveStorage(hass, "archive-entry", "archive-agent")
    previous = _turn(
        timestamp="2026-10-01T10:00:00+00:00", sequence=3, user_text="Prior turn"
    )
    proposed = _turn(turn_id="appended", sequence=sequence, user_text="New turn")
    await storage.async_save_metadata(
        {
            "sessions": [asdict(_session(turn_count=2))],
            "active": {},
            "partitions": ["2026-09", "2026-10"],
            "pending_append": {"turn": asdict(proposed), "partition_existed": True},
        }
    )
    await storage.async_save_partition("2026-09", {"turns": []})
    await storage.async_save_partition("2026-10", {"turns": [asdict(previous)]})
    paths = [
        Path(storage._metadata.path),
        Path(storage._partition_store("2026-09").path),
        Path(storage._partition_store("2026-10").path),
    ]
    original = [path.read_bytes() for path in paths]
    for _ in range(2):
        fresh_storage = HomeAssistantArchiveStorage(
            hass, "archive-entry", "archive-agent"
        )
        restarted = ConversationArchive(fresh_storage, "archive-agent")
        with pytest.raises(ValueError, match="transaction is corrupted"):
            await restarted.async_initialize()
        assert not restarted.initialized
        assert [path.read_bytes() for path in paths] == original
