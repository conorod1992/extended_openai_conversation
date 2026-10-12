"""Production restore journal writer/reader contracts with real HA Store disk I/O.

Keep this modest: reuse the OS-store fixture instead of booting another HA
process for each payload. The existing process-crash suite covers cold boots.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    restore_recovery,
)
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
    assert len(read_target.memories) == len(target.memories)
    assert len(read_rollback.knowledge) == len(rollback.knowledge)
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
