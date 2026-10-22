"""Ownership mapping does not revalidate detached facts on HA's loop."""

from copy import deepcopy
import threading

import pytest

from custom_components.extended_openai_conversation_responses import backup, transfer
from tests.test_backup import _document


@pytest.mark.parametrize("mapping", [{}, {"user-1": "user-1"}, {"user-1": "user-2"}])
async def test_mapping_memory_validation_runs_in_worker(hass, monkeypatch, mapping):
    document = _document()
    base = document["memories"]["memories"][0]
    document["memories"]["memories"] = [
        {
            **base,
            "memory_id": f"memory-{i}",
            "content": f"My favourite item is number {i}. "
            + "Personal preference. " * 20,
        }
        for i in range(2000)
    ]
    prepared = await transfer.async_inspect_transfer(document, "agent")
    original = deepcopy(prepared)
    loop_thread = threading.get_ident()
    threads = []
    validate = backup.PersistentMemory.validate_backup_data

    def tracked(data):
        threads.append(threading.get_ident())
        return validate(data)

    monkeypatch.setattr(
        backup.PersistentMemory, "validate_backup_data", staticmethod(tracked)
    )
    mapped = await transfer.async_apply_user_scope_mappings(
        prepared, {transfer.SECTION_PERSISTENT_MEMORY}, mapping
    )
    assert threads and all(thread != loop_thread for thread in threads)
    assert len(mapped.memories) == 2000
    assert mapped.memories[0].user_id == mapping.get(base["user_id"], base["user_id"])
    assert prepared == original


async def test_mapping_still_rejects_colliding_canonical_keys(hass):
    document = _document()
    base = document["memories"]["memories"][0]
    document["memories"]["memories"] = [
        {**base, "memory_id": "first", "user_id": "user-a", "key": "preference"},
        {**base, "memory_id": "second", "user_id": "user-b", "key": "preference"},
    ]
    prepared = await transfer.async_inspect_transfer(document, "agent")
    original = deepcopy(prepared)
    with pytest.raises(backup.BackupError, match=r"ownership mapping|canonical key"):
        await transfer.async_apply_user_scope_mappings(
            prepared,
            {transfer.SECTION_PERSISTENT_MEMORY},
            {"user-a": "user-c", "user-b": "user-c"},
        )
    assert prepared == original
