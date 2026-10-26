"""Transfer cleanup keeps native workers owned through repeated caller cancellation."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import (
    backup_transfer,
    transfer_staging,
)


class HeldExecutor:
    """Hold a real thread before its filesystem effect, without cancelling it."""

    def __init__(self, target, *, failure: bool, fail_before_effect: bool) -> None:
        self.target = target
        self.failure = failure
        self.fail_before_effect = fail_before_effect
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.task: asyncio.Task | None = None
        self.attempts = 0

    def run(self, function, *args):
        if function is not self.target:
            return function(*args)
        self.attempts += 1
        self.entered.set()
        try:
            if not self.release.wait(10):
                raise TimeoutError("Test did not release the native transfer worker")
            if self.failure and self.fail_before_effect:
                raise OSError("Controlled transfer worker failure")
            result = function(*args)
            if self.failure:
                # Allocation can create a file and lose the response containing
                # its identity. The existing staging scan must retain ownership.
                raise OSError("Controlled transfer worker failure after effect")
            return result
        finally:
            self.finished.set()

    async def execute(self, function, *args):
        if function is self.target:
            self.task = asyncio.current_task()
        return await asyncio.to_thread(self.run, function, *args)


@pytest.mark.parametrize("kind", ["append", "remove", "build", "create"])
@pytest.mark.parametrize("cancels", [1, 3, 5])
@pytest.mark.parametrize("failure", [False, True])
async def test_transfer_native_worker_stays_owned_until_settled(
    tmp_path: Path, monkeypatch, kind: str, cancels: int, failure: bool
) -> None:
    """One cancellation is the control; later cancels cannot release its lease."""
    monkeypatch.setattr(
        transfer_staging,
        "async_get_transfer_staging",
        AsyncMock(return_value=tmp_path),
    )
    monkeypatch.setattr(backup_transfer, "_schedule_expiry", lambda _hass: None)
    path = tmp_path / "extended-openai-backup-upload-fixed"

    def build_archive(_snapshot, directory):
        archive = Path(directory) / "extended-openai-backup-archive-fixed.zip"
        archive.write_bytes(b"archive")
        return {"path": str(archive), "size": 7, "filename": "archive.zip"}

    if kind == "build":
        monkeypatch.setattr(backup_transfer, "_build_archive_file", build_archive)
        target = build_archive
    elif kind == "create":
        target = backup_transfer._create_upload_file
    elif kind == "append":
        path.write_bytes(b"")
        target = backup_transfer._decode_append_file
    else:
        path.write_bytes(b"seed")
        target = backup_transfer._remove_file

    worker = HeldExecutor(target, failure=failure, fail_before_effect=kind == "remove")
    hass = SimpleNamespace(data={}, async_add_executor_job=worker.execute)
    # Transfer starts serialize their unregistered allocations. Per-file append
    # and removal are normally owned by a session lock. Exercise those real
    # lock types around the unchanged production executor-wrapper entry points.
    lease = (
        backup_transfer._start_lock(hass)
        if kind in {"build", "create"}
        else asyncio.Lock()
    )

    async def owned_operation():
        try:
            async with lease:
                if kind == "append":
                    return await backup_transfer._async_append_file(
                        hass, str(path), base64.b64encode(b"late").decode(), 4
                    )
                if kind == "remove":
                    return await backup_transfer._async_remove_path(hass, str(path))
                if kind == "build":
                    return await backup_transfer._async_build_archive_file(hass, {})
                return await backup_transfer._async_create_upload_file(hass)
        except asyncio.CancelledError:
            if kind == "append":
                # The import path discards/unlinks its upload after leaving the
                # append lease. A late native open(..., "ab") must not recreate it.
                path.unlink(missing_ok=True)
            raise

    operation = asyncio.create_task(owned_operation())
    try:
        assert await asyncio.to_thread(worker.entered.wait, 2)
        assert worker.task is not None
        for index in range(cancels):
            operation.cancel(
                "original transfer cancellation"
                if index == 0
                else f"later cancellation {index}"
            )
            await asyncio.sleep(0)

        # The old bare-await handler cancels this asyncio wrapper while its
        # underlying thread is still running. Let its existing early cleanup
        # finish so a later allocation/append exposes any unregistered file.
        cancelled_native_wrapper = worker.task.cancelled()
        if cancelled_native_wrapper:
            result = await asyncio.gather(operation, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError)
        before_release = {
            "caller_pending": not operation.done(),
            "lease_owned": lease.locked(),
            "native_wrapper_pending": not worker.task.done(),
            "native_thread_pending": not worker.finished.is_set(),
        }

        worker.release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await operation
        assert await asyncio.to_thread(worker.finished.wait, 2)
        assert str(cancelled.value) == "original transfer cancellation"
        assert worker.attempts == 1
        assert not lease.locked()

        pending = backup_transfer._pending(hass)
        files = {str(item) for item in tmp_path.iterdir() if item.is_file()}
        if kind == "remove" and failure:
            assert path.exists()
            assert str(path) in pending
            assert pending[str(path)].attempts == 1
            assert pending[str(path)].retry_at > 0
        elif kind in {"build", "create"} and failure:
            assert len(files) == 1
            assert files <= pending.keys(), (
                "lost allocation identities remain registered for cleanup"
            )
            assert all(
                pending[item].size > 0 or Path(item).stat().st_size == 0
                for item in files
            )
        else:
            assert files == set(), (
                "cancelled transfer left a late recreated/unregistered file"
            )
            assert not pending
        assert before_release == {
            "caller_pending": True,
            "lease_owned": True,
            "native_wrapper_pending": True,
            "native_thread_pending": True,
        }, "repeated cancellation escaped before the native operation settled"
    finally:
        worker.release.set()
        await asyncio.to_thread(worker.finished.wait, 2)
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("kind", ["build", "create"])
@pytest.mark.parametrize("cleanup_cancels", [1, 3])
async def test_transfer_allocation_retains_original_cancellation_through_cleanup(
    tmp_path: Path, monkeypatch, kind: str, cleanup_cancels: int
) -> None:
    """Late path cleanup keeps the allocation lease and first cancellation."""
    monkeypatch.setattr(
        transfer_staging,
        "async_get_transfer_staging",
        AsyncMock(return_value=tmp_path),
    )
    monkeypatch.setattr(backup_transfer, "_schedule_expiry", lambda _hass: None)

    def build_archive(_snapshot, directory):
        archive = Path(directory) / "extended-openai-backup-archive-fixed.zip"
        archive.write_bytes(b"archive")
        return {"path": str(archive), "size": 7, "filename": "archive.zip"}

    if kind == "build":
        monkeypatch.setattr(backup_transfer, "_build_archive_file", build_archive)
        target = build_archive
    else:
        target = backup_transfer._create_upload_file
    allocation = HeldExecutor(target, failure=False, fail_before_effect=False)
    cleanup = HeldExecutor(
        backup_transfer._remove_file, failure=False, fail_before_effect=False
    )

    async def execute(function, *args):
        if function is allocation.target:
            return await allocation.execute(function, *args)
        if function is cleanup.target:
            return await cleanup.execute(function, *args)
        return await asyncio.to_thread(function, *args)

    hass = SimpleNamespace(data={}, async_add_executor_job=execute)
    lease = backup_transfer._start_lock(hass)

    async def owned_operation():
        async with lease:
            if kind == "build":
                return await backup_transfer._async_build_archive_file(hass, {})
            return await backup_transfer._async_create_upload_file(hass)

    operation = asyncio.create_task(owned_operation())
    try:
        assert await asyncio.to_thread(allocation.entered.wait, 2)
        operation.cancel("original allocation cancellation")
        await asyncio.sleep(0)
        allocation.release.set()
        assert await asyncio.to_thread(cleanup.entered.wait, 2)
        assert allocation.finished.is_set()
        assert len(list(tmp_path.iterdir())) == 1
        for index in range(cleanup_cancels):
            operation.cancel(f"cleanup cancellation {index + 1}")
            await asyncio.sleep(0)
        assert not operation.done()
        assert lease.locked()
        assert cleanup.task is not None and not cleanup.task.done()
        assert not cleanup.finished.is_set()

        cleanup.release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await operation
        assert str(cancelled.value) == "original allocation cancellation"
        assert allocation.attempts == cleanup.attempts == 1
        assert cleanup.finished.is_set()
        assert not lease.locked()
        assert not list(tmp_path.iterdir())
        assert not backup_transfer._pending(hass)
    finally:
        allocation.release.set()
        cleanup.release.set()
        await asyncio.to_thread(allocation.finished.wait, 2)
        await asyncio.to_thread(cleanup.finished.wait, 2)
        await asyncio.gather(operation, return_exceptions=True)
