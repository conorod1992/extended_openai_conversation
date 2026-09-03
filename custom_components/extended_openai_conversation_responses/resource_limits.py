"""Shared resource limits for data handled by model-facing tools."""

from __future__ import annotations

from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

MAX_ATTACHMENT_COUNT = 10
MAX_LOCAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_REMOTE_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_NATIVE_SERVICE_ACTIONS = 20


def _validate_size(path: Path, size: int, total_bytes: int) -> None:
    """Validate one local file against per-file and aggregate limits."""
    if size > MAX_LOCAL_ATTACHMENT_BYTES:
        raise HomeAssistantError(
            f"`{path}` is too large; local attachments are limited to "
            f"{MAX_LOCAL_ATTACHMENT_BYTES} bytes each"
        )
    if total_bytes + size > MAX_TOTAL_ATTACHMENT_BYTES:
        raise HomeAssistantError(
            "Local attachments exceed the combined request limit of "
            f"{MAX_TOTAL_ATTACHMENT_BYTES} bytes"
        )


def bounded_local_file_size(path: Path, total_bytes: int = 0) -> int:
    """Return a local file size after enforcing per-file and request limits."""
    try:
        size = path.stat().st_size
    except OSError as err:
        raise HomeAssistantError(f"Unable to read `{path}`: {err}") from err
    _validate_size(path, size, total_bytes)
    return size


def read_bounded_local_file(path: Path, total_bytes: int = 0) -> bytes:
    """Read a local file without allowing it to exceed attachment limits."""
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_LOCAL_ATTACHMENT_BYTES + 1)
    except OSError as err:
        raise HomeAssistantError(f"Unable to read `{path}`: {err}") from err

    _validate_size(path, len(content), total_bytes)
    return content
