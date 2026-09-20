"""Regression tests for scalable backup restore recovery."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup,
    backup_transfer,
    restore_recovery,
)


def test_restore_journal_uses_scalable_backup_limit(monkeypatch) -> None:
    """Pending recovery must accept the same bounded payload size as chunked restore."""
    calls: list[tuple[object, str, int]] = []
    target = object()
    rollback = object()

    def inspect(value, subentry_id, *, max_bytes):
        calls.append((value, subentry_id, max_bytes))
        return target if len(calls) == 1 else rollback

    monkeypatch.setattr(backup, "inspect_backup", inspect)
    journal = {
        "transaction_id": "transaction-1",
        "entry_id": "entry-1",
        "subentry_id": "agent-1",
        "phase": "applying",
        "created_at": "2026-09-07T12:00:00+00:00",
        "target": {"large": "target"},
        "rollback": {"large": "rollback"},
    }

    loaded, loaded_target, loaded_rollback = restore_recovery._load_journal(
        journal, "entry-1", "agent-1"
    )

    assert loaded == journal
    assert loaded_target is target
    assert loaded_rollback is rollback
    assert calls == [
        (journal["target"], "agent-1", backup.MAX_BACKUP_BYTES),
        (journal["rollback"], "agent-1", backup.MAX_BACKUP_BYTES),
    ]


async def test_delete_sessions_preserves_first_of_multiple_cancellations(monkeypatch):
    """Later cancellation must not replace the cancellation already being propagated."""
    first_error = asyncio.CancelledError("first")
    second_error = asyncio.CancelledError("second")
    sessions = [SimpleNamespace(path="first"), SimpleNamespace(path="second")]
    calls: list[str] = []

    async def delete(_hass, session):
        calls.append(session.path)
        raise first_error if session is sessions[0] else second_error

    monkeypatch.setattr(backup_transfer, "_async_delete_session_file", delete)

    with pytest.raises(asyncio.CancelledError) as raised:
        await backup_transfer._async_delete_sessions(object(), sessions)

    assert raised.value is first_error
    assert calls == ["first", "second"]


async def test_remove_path_finishes_executor_cleanup_before_cancellation(tmp_path):
    """Cancellation cannot leave an executor-backed delete operation unfinished."""
    path = tmp_path / "pending-delete"
    path.write_bytes(b"data")
    release = asyncio.Event()

    async def executor_job(func, *args):
        await release.wait()
        return func(*args)

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_remove_path(hass, str(path)))
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()


async def test_delete_sessions_continues_after_one_cleanup_is_cancelled(monkeypatch):
    """All detached files are attempted before cancellation is re-raised."""
    first = SimpleNamespace(path="first")
    second = SimpleNamespace(path="second")
    calls: list[str] = []

    async def delete(_hass, session):
        calls.append(session.path)
        if session is first:
            raise asyncio.CancelledError

    monkeypatch.setattr(backup_transfer, "_async_delete_session_file", delete)

    with pytest.raises(asyncio.CancelledError):
        await backup_transfer._async_delete_sessions(object(), [first, second])
    assert calls == ["first", "second"]


async def test_async_build_archive_cancellation_cleans_late_result(
    tmp_path, monkeypatch
):
    path = tmp_path / "late.zip"
    path.write_bytes(b"archive")
    removed = AsyncMock()
    monkeypatch.setattr(backup_transfer, "_async_remove_path", removed)

    async def executor_job(_func, _snapshot):
        await asyncio.sleep(0.01)
        return {"path": str(path)}

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_build_archive_file(hass, {}))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    removed.assert_awaited_once_with(hass, str(path))


async def test_async_build_archive_cancellation_preserves_cancellation_if_worker_fails():
    async def executor_job(_func, _snapshot):
        await asyncio.sleep(0.01)
        raise RuntimeError("worker failed")

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_build_archive_file(hass, {}))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_async_create_upload_cancellation_cleans_late_file(tmp_path, monkeypatch):
    path = tmp_path / "late-upload"
    removed = AsyncMock()
    monkeypatch.setattr(backup_transfer, "_async_remove_path", removed)

    async def executor_job(_func):
        await asyncio.sleep(0.01)
        return str(path)

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_create_upload_file(hass))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    removed.assert_awaited_once_with(hass, str(path))


async def test_async_create_upload_cancellation_preserves_cancellation_if_worker_fails():
    async def executor_job(_func):
        await asyncio.sleep(0.01)
        raise OSError("disk failed")

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(backup_transfer._async_create_upload_file(hass))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_async_append_waits_for_executor_before_propagating_cancellation(
    tmp_path,
):
    path = tmp_path / "upload"
    path.write_bytes(b"")
    release = asyncio.Event()

    async def executor_job(func, *args):
        await release.wait()
        return func(*args)

    hass = SimpleNamespace(async_add_executor_job=executor_job)
    task = asyncio.create_task(
        backup_transfer._async_append_file(
            hass, str(path), base64.b64encode(b"data").decode(), 4
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert path.read_bytes() == b"data"
