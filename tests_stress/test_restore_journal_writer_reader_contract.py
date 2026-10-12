"""Production restore journal writer/reader contracts with real HA Store disk I/O.

Keep this modest: reuse the OS-store fixture instead of booting another HA
process for each payload. The existing process-crash suite covers cold boots.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
from custom_components.extended_openai_conversation_responses.guest_mode import GuestModeSchedule
from tests.test_conversation_archive import _session, _turn
from tests.test_restore_recovery import _states
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("phase", ["applying", "committed"])
@pytest.mark.parametrize("extra", ["", ("é\n東京 " + chr(34) + "quoted" + chr(34) + " \\ path ") * 200])
async def test_accepted_journal_is_readable_by_production_reader_on_disk(
    hass, phase, extra
):
    """Successful journal verification must imply a new Store can read and validate it."""
    target, rollback = _states()
    target.config = {**target.config, "prompt": "target " + extra}
    rollback.config = {**rollback.config, "prompt": "rollback " + extra}
    for label, prepared in (("target", target), ("rollback", rollback)):
        # Exercise real content in every durable category. Empty archive and
        # Guest Mode sections cannot witness lost transcript/schedule fields.
        # Stay within record/title limits while retaining every escape shape;
        # the configuration prompt above carries the full long payload.
        text = label + " " + extra[:80]
        prepared.memories = [replace(row, content=text) for row in prepared.memories]
        prepared.temporary_memories = [replace(row, content=text) for row in prepared.temporary_memories]
        prepared.knowledge = [replace(row, content=text) for row in prepared.knowledge]
        prepared.archive_sessions = [replace(
            _session(), agent_subentry_id="agent-new", title=text,
        )]
        prepared.archive_turns = [_turn(user_text=text, assistant_text="reply " + text)]
        prepared.guest_mode_schedule = GuestModeSchedule(
            active_from=prepared.created_at, active_until=None,
            source=label, updated_at=prepared.created_at,
        )
        prepared.request_rules["rules"][0]["name"] = label + " rule"
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
    journal["phase"] = phase
    # Confirm writer-side acceptance before saving any journal.
    for section in ("target", "rollback"):
        backup.inspect_backup(
            journal[section], "agent-new",
            max_bytes=backup.MAX_BACKUP_BYTES, private_journal=True,
        )
    store = restore_recovery._journal_store(hass, "entry-1", "agent-new")
    assert await restore_recovery._async_write_journal_verified(store, journal)
    path = Path(store.path)
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["data"] == journal
    # A fresh production Store instance must not reuse the first Store's cache.
    reader = restore_recovery._journal_store(hass, "entry-1", "agent-new")
    # Read using the actual strict disk reader, including version/key checks.
    reloaded = await reader._async_load_data()
    assert reloaded == journal
    loaded, read_target, read_rollback = restore_recovery._load_journal(
        reloaded, "entry-1", "agent-new"
    )
    assert loaded == journal
    assert read_target.title == target.title
    assert read_rollback.title == rollback.title
    assert read_target.config["prompt"] == target.config["prompt"]
    assert read_rollback.config["prompt"] == rollback.config["prompt"]
    # Count-only checks can miss field corruption. Compare the complete
    # serialized durable categories using the production format.
    for section in ("memories", "temporary_memories", "knowledge", "archive",
                    "usage", "guest_mode", "request_rules"):
        assert restore_recovery._prepared_document(
            read_target, "entry-1", "agent-new"
        )[section] == journal["target"][section]
        assert restore_recovery._prepared_document(
            read_rollback, "entry-1", "agent-new"
        )[section] == journal["rollback"][section]
    await store.async_remove()
    assert not path.exists()


@pytest.mark.usefixtures("real_store_io")
async def test_corrupt_disk_journal_cannot_be_accepted_or_silently_removed(hass):
    """A damaged envelope remains available for repair; no valid backup is fabricated."""
    target, rollback = _states()
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
    store = restore_recovery._journal_store(hass, "entry-1", "agent-new")
    assert await restore_recovery._async_write_journal_verified(store, journal)
    path = Path(store.path)
    original = path.read_bytes()
    damaged = original[: len(original) // 2]
    path.write_bytes(damaged)
    fresh = restore_recovery._journal_store(hass, "entry-1", "agent-new")
    with pytest.raises(backup.BackupError, match="unreadable|corrupted"):
        await fresh._async_load_data()
    assert path.read_bytes() == damaged
    assert not list(path.parent.glob(path.name + ".corrupt*"))
    # Restore the authoritative document, then verify a distinct reader works.
    path.write_bytes(original)
    assert await restore_recovery._journal_store(
        hass, "entry-1", "agent-new"
    )._async_load_data() == journal
    await store.async_remove()
    assert not path.exists()


@pytest.mark.usefixtures("real_store_io")
@pytest.mark.parametrize("corruption", ["wrong-key", "wrong-version", "missing-data"])
async def test_reader_rejects_foreign_or_incomplete_store_envelopes(
    hass, corruption
):
    """The reader must not reinterpret another store's bytes as our journal."""
    target, rollback = _states()
    journal = restore_recovery._new_journal("entry-1", "agent-new", target, rollback)
    store = restore_recovery._journal_store(hass, "entry-1", "agent-new")
    assert await restore_recovery._async_write_journal_verified(store, journal)
    path = Path(store.path)
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "wrong-key":
        envelope["key"] += ".sibling"
    elif corruption == "wrong-version":
        envelope["version"] += 1
    else:
        envelope.pop("data")
    path.write_text(json.dumps(envelope), encoding="utf-8")
    damaged = path.read_bytes()
    reader = restore_recovery._journal_store(hass, "entry-1", "agent-new")
    with pytest.raises(backup.BackupError, match="corrupted"):
        await reader._async_load_data()
    assert path.read_bytes() == damaged
    assert not list(path.parent.glob(path.name + ".corrupt*"))
    await store.async_remove()
