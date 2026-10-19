"""Full-state preview hashes must yield without losing cancellation checks."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer as bt,
    transfer,
)
from tests.test_remaining_backup_contracts import _import, _Snapshot


@pytest.mark.parametrize("cancel", [False, True])
async def test_restore_hash_is_off_loop_and_cancel_cannot_commit(
    hass, tmp_path, monkeypatch, cancel
):
    session = _import(hass, tmp_path)
    prepared = transfer.PreparedTransfer(
        source_kind="full_backup",
        mode="full",
        title="Preview",
        available_sections=frozenset({transfer.SECTION_CONFIGURATION}),
        created_at=None,
        integration_version=None,
        config={},
    )
    monkeypatch.setattr(
        bt, "_async_load_prepared_restore", AsyncMock(return_value=prepared)
    )
    monkeypatch.setattr(
        transfer,
        "_current_snapshot",
        AsyncMock(return_value=_Snapshot({"prompt": "original"})),
    )
    monkeypatch.setattr(bt, "_async_remove_path", AsyncMock())
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    threads = []
    fingerprint = bt._snapshot_revision

    cancelled = threading.Event()

    def cancel_upload():
        bt._imports(hass).pop(session.session_id, None)
        cancelled.set()

    def observed(value):
        thread = threading.get_ident()
        threads.append(thread)
        if cancel:
            if thread == loop_thread:
                cancel_upload()
            else:
                loop.call_soon_threadsafe(cancel_upload)
                # The worker returns only after the intended race has happened.
                # This synchronizes the fixture; it never sleeps on HA's loop.
                assert cancelled.wait(5), "Loop did not process upload cancellation"
        return fingerprint(value)

    monkeypatch.setattr(bt, "_snapshot_revision", observed)
    writes = []

    async def restore(*args, precondition, **kwargs):
        await precondition()
        writes.append(True)
        return {"status": "restored"}

    monkeypatch.setattr(transfer, "async_restore_transfer", restore)
    operation = bt._restore_import(
        hass,
        SimpleNamespace(entry_id="entry"),
        SimpleNamespace(subentry_id="agent"),
        {"session_id": "session", "preview_token": "preview"},
    )
    if cancel:
        with pytest.raises(backup.BackupError, match="expired or was cancelled"):
            await operation
        assert not writes
    else:
        assert (await operation)["status"] == "restored"
        assert writes == [True]
    assert threads and all(thread != loop_thread for thread in threads)
