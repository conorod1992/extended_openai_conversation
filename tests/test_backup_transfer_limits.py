"""Regression tests for full-backup transfer resource limits."""

from __future__ import annotations

import asyncio
import os

import pytest

from custom_components.extended_openai_conversation_responses import backup
from custom_components.extended_openai_conversation_responses import backup_transfer


async def test_import_quota_is_checked_before_temp_file(hass, monkeypatch) -> None:
    """An over-quota upload must not allocate even a temporary file."""
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_TEMP_BYTES", 4)
    allocated = False

    async def allocate(_hass):
        nonlocal allocated
        allocated = True
        raise AssertionError("quota rejection must happen before temp-file allocation")

    monkeypatch.setattr(backup_transfer, "_async_create_upload_file", allocate)

    with pytest.raises(backup.BackupError, match="temporary storage quota"):
        await backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "backup.json", "size": 5},
        )

    assert allocated is False
    assert backup_transfer._imports(hass) == {}


async def test_concurrent_transfer_starts_cannot_bypass_session_limit(
    hass, monkeypatch, tmp_path
) -> None:
    """A second start waits for the first reservation, then sees its registered slot."""
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_SESSIONS", 1)
    monkeypatch.setattr(backup_transfer, "MAX_TRANSFER_TEMP_BYTES", 1024)
    allocation_started = asyncio.Event()
    finish_allocation = asyncio.Event()
    allocations = 0

    async def allocate(_hass):
        nonlocal allocations
        allocations += 1
        allocation_started.set()
        await finish_allocation.wait()
        path = tmp_path / f"upload-{allocations}.tmp"
        path.write_bytes(b"")
        return str(path)

    monkeypatch.setattr(backup_transfer, "_async_create_upload_file", allocate)

    first_task = asyncio.create_task(
        backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "first.json", "size": 4},
        )
    )
    await allocation_started.wait()
    second_task = asyncio.create_task(
        backup_transfer._start_import(
            hass,
            "entry-1",
            "agent-1",
            {"filename": "second.json", "size": 4},
        )
    )
    await asyncio.sleep(0)
    assert second_task.done() is False

    finish_allocation.set()
    first = await first_task
    try:
        with pytest.raises(backup.BackupError, match="Too many full backup transfers"):
            await second_task
        assert allocations == 1
    finally:
        session = backup_transfer._imports(hass).get(first["session_id"])
        path = session.path if session is not None else None
        await backup_transfer._discard_import(hass, first["session_id"])
        if path is not None:
            assert not os.path.exists(path)
