"""Native staging locks protect other live owners and unrelated filesystem data."""

import asyncio
import fcntl
from types import SimpleNamespace
import threading

import pytest

from custom_components.extended_openai_conversation_responses import transfer_staging as staging

from custom_components.extended_openai_conversation_responses.transfer_staging import (
    _initialize_owner,
)


def test_live_owner_and_unmarked_data_survive_reconciliation(tmp_path):
    root = tmp_path / "private"
    first = _initialize_owner(root)
    second = None
    payload = first.directory / "extended-openai-backup-live.zip"
    payload.write_bytes(b"LIVE-OWNER")
    unknown = root / f"owner-{'a' * 32}"
    unknown.mkdir()
    (unknown / "sentinel").write_text("UNMARKED")
    empty_unmarked = root / f"owner-{'b' * 32}"
    empty_unmarked.mkdir()
    (root / "unrelated-sentinel").write_text("KEEP")
    try:
        second = _initialize_owner(root)
        assert payload.read_bytes() == b"LIVE-OWNER"
        assert (unknown / "sentinel").read_text() == "UNMARKED"
        assert not empty_unmarked.exists()
        assert (root / "unrelated-sentinel").read_text() == "KEEP"
        assert first.directory != second.directory
    finally:
        first.close()
        if second is not None:
            second.close()


def test_unlocked_owned_payload_is_reclaimed_without_session_authority(tmp_path):
    root = tmp_path / "private"
    abandoned = _initialize_owner(root)
    payload = abandoned.directory / "extended-openai-backup-orphan.zip"
    payload.write_bytes(b"ORPHAN")
    abandoned.lock_handle.close()  # OS boundary left by process termination
    fresh = _initialize_owner(root)
    try:
        assert not abandoned.directory.exists()
        assert list(fresh.directory.glob("extended-openai-backup-*")) == []
    finally:
        fresh.close()


def test_owner_close_is_idempotent(tmp_path):
    owner = _initialize_owner(tmp_path / "private")

    owner.close()
    owner.close()

    assert not owner.directory.exists()


def test_staging_root_symlink_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "staging-link"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError, match="must not be a symbolic link"):
        _initialize_owner(root)


def test_owner_lock_failure_releases_lock_and_allows_retry(tmp_path, monkeypatch):
    root = tmp_path / "private"
    original_flock = fcntl.flock

    def fail_nonblocking_owner_lock(file_descriptor, operation):
        if operation & fcntl.LOCK_NB:
            raise OSError("owner lock unavailable")
        return original_flock(file_descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", fail_nonblocking_owner_lock)
    with pytest.raises(OSError, match="owner lock unavailable"):
        _initialize_owner(root)

    monkeypatch.setattr(fcntl, "flock", original_flock)
    owner = _initialize_owner(root)
    try:
        assert owner.directory.is_dir()
    finally:
        owner.close()


def _hass(tmp_path):
    async def executor(target, *args):
        return await asyncio.to_thread(target, *args)

    return SimpleNamespace(data={}, config=SimpleNamespace(config_dir=str(tmp_path)),
                           async_add_executor_job=executor)


async def test_failed_allocation_retries_and_reclaims_only_owned_residue(tmp_path, monkeypatch):
    hass = _hass(tmp_path)
    root = tmp_path / ".storage" / f"{staging.DOMAIN}.transfer-staging"
    root.mkdir(parents=True)
    unrelated = root / "unrelated"
    unrelated.write_text("KEEP")
    lock_file = staging._lock_file
    attempts = 0

    def fail_once(path):
        nonlocal attempts
        if path.name == "owner.lock":
            attempts += 1
            if attempts == 1:
                raise PermissionError("temporary owner allocation failure")
        return lock_file(path)

    monkeypatch.setattr(staging, "_lock_file", fail_once)
    with pytest.raises(PermissionError, match="temporary"):
        await staging.async_get_transfer_staging(hass)
    assert staging._OWNER_KEY not in hass.data
    directory = await staging.async_get_transfer_staging(hass)
    assert directory.is_dir()
    assert attempts == 2
    assert list(root.glob("owner-*")) == [directory]
    assert unrelated.read_text() == "KEEP"
    await staging.async_close_transfer_staging(hass)
    assert list(root.glob("owner-*")) == []


async def test_shared_allocation_survives_waiter_cancellation(tmp_path, monkeypatch):
    hass = _hass(tmp_path)
    initialize = staging._initialize_owner
    entered, release = threading.Event(), threading.Event()
    attempts = 0

    def held(root):
        nonlocal attempts
        attempts += 1
        entered.set()
        assert release.wait(10)
        return initialize(root)

    monkeypatch.setattr(staging, "_initialize_owner", held)
    first = asyncio.create_task(staging.async_get_transfer_staging(hass))
    second = asyncio.create_task(staging.async_get_transfer_staging(hass))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        cached = hass.data[staging._OWNER_KEY]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert hass.data[staging._OWNER_KEY] is cached
        assert not cached.done()
        release.set()
        directory = await second
        assert await staging.async_get_transfer_staging(hass) == directory
        assert hass.data[staging._OWNER_KEY] is cached
        assert attempts == 1
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        await staging.async_close_transfer_staging(hass)


async def test_close_without_an_allocated_owner_is_safe(tmp_path) -> None:
    await staging.async_close_transfer_staging(_hass(tmp_path))


async def test_failed_shared_attempt_with_cancelled_waiters_is_retryable(tmp_path, monkeypatch):
    hass = _hass(tmp_path)
    initialize = staging._initialize_owner
    entered, release = threading.Event(), threading.Event()

    def held_failure(root):
        entered.set()
        assert release.wait(10)
        raise PermissionError("late allocation failure")

    monkeypatch.setattr(staging, "_initialize_owner", held_failure)
    waiter = asyncio.create_task(staging.async_get_transfer_staging(hass))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        shared = hass.data[staging._OWNER_KEY]
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        with pytest.raises(PermissionError):
            await shared
        assert staging._OWNER_KEY not in hass.data
        monkeypatch.setattr(staging, "_initialize_owner", initialize)
        assert (await staging.async_get_transfer_staging(hass)).is_dir()
    finally:
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)
        await staging.async_close_transfer_staging(hass)


async def test_failed_callback_cannot_discard_replacement_owner(tmp_path):
    hass = _hass(tmp_path)
    stale = asyncio.get_running_loop().create_future()
    stale.set_exception(PermissionError("stale failure"))
    replacement = asyncio.get_running_loop().create_future()
    hass.data[staging._OWNER_KEY] = replacement
    staging._discard_failed_initialization(hass, stale)
    assert hass.data[staging._OWNER_KEY] is replacement
    replacement.cancel()
    staging._discard_failed_initialization(hass, replacement)
    assert staging._OWNER_KEY not in hass.data
