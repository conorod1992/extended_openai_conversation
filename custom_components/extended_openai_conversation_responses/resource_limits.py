"""Shared resource limits for local files sent to model providers."""

from __future__ import annotations

from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

MAX_ATTACHMENT_COUNT = 10
MAX_LOCAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024


def bounded_local_file_size(path: Path, total_bytes: int = 0) -> int:
    """Return a local file size after enforcing per-file and request limits."""
    try:
        size = path.stat().st_size
    except OSError as err:
        raise HomeAssistantError(f"Unable to read `{path}`: {err}") from err

    if size > MAX_LOCAL_ATTACHMENT_BYTES:
        raise HomeAssistantError(
            f"`{path}` is too large ({size} bytes); local attachments are limited "
            f"to {MAX_LOCAL_ATTACHMENT_BYTES} bytes each"
        )
    if total_bytes + size > MAX_TOTAL_ATTACHMENT_BYTES:
        raise HomeAssistantError(
            "Local attachments exceed the combined request limit of "
            f"{MAX_TOTAL_ATTACHMENT_BYTES} bytes"
        )
    return size
