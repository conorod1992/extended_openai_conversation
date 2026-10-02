"""Private transfer payload ownership surviving loss of process-local sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
from typing import BinaryIO
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_OWNER_KEY = f"{DOMAIN}.transfer_staging_owner"
_OWNER_NAME = re.compile(r"owner-[0-9a-f]{32}")


def _lock_file(path: Path) -> int:
    # Home Assistant's supported runtime is POSIX. Do not follow a substituted
    # ownership marker outside the private staging root.
    return os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)


@dataclass
class TransferStagingOwner:
    """Keep a native owner lock for the entire HA transfer registry lifetime."""

    directory: Path
    lock_handle: BinaryIO

    def close(self) -> None:
        """Remove this owner's settled files and release native ownership."""
        if self.lock_handle.closed:
            return
        try:
            shutil.rmtree(self.directory)
        finally:
            self.lock_handle.close()


def _initialize_owner(root: Path) -> TransferStagingOwner:
    import fcntl

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise OSError("Transfer staging root must not be a symbolic link")
    root_fd = _lock_file(root / "root.lock")
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        for directory in root.iterdir():
            if (
                not _OWNER_NAME.fullmatch(directory.name)
                or directory.is_symlink()
                or not directory.is_dir()
            ):
                continue
            # Missing authority can only be reclaimed when the slot is empty.
            # Preserve files in unrecognized/unmarked directories.
            if not (directory / "owner.lock").exists():
                if not any(directory.iterdir()):
                    directory.rmdir()
                continue
            owner_fd = _lock_file(directory / "owner.lock")
            try:
                try:
                    fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue  # Never reclaim another live process/HA instance.
                shutil.rmtree(directory)
            finally:
                os.close(owner_fd)
        directory = root / f"owner-{uuid4().hex}"
        directory.mkdir(mode=0o700)
        owner_fd = _lock_file(directory / "owner.lock")
        try:
            fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(owner_fd)
            raise
        return TransferStagingOwner(directory, os.fdopen(owner_fd, "rb"))
    finally:
        os.close(root_fd)


async def async_get_transfer_staging(hass: HomeAssistant) -> Path:
    """Reconcile abandoned private owners once, without reviving sessions."""
    task: asyncio.Future[TransferStagingOwner] | None = hass.data.get(_OWNER_KEY)
    if task is None:
        root = Path(hass.config.config_dir) / ".storage" / f"{DOMAIN}.transfer-staging"
        task = asyncio.ensure_future(
            hass.async_add_executor_job(_initialize_owner, root)
        )
        hass.data[_OWNER_KEY] = task
    owner = await asyncio.shield(task)
    return owner.directory


async def async_close_transfer_staging(hass: HomeAssistant) -> None:
    """Settle any allocation before releasing this instance's native owner."""
    task = hass.data.pop(_OWNER_KEY, None)
    if task is not None:
        owner = await asyncio.shield(task)
        await hass.async_add_executor_job(owner.close)
