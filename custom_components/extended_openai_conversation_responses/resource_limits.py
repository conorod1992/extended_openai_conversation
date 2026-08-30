"""Shared resource limits for local files sent to model providers."""

from __future__ import annotations

from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

MAX_ATTACHMENT_COUNT = 10
MAX_LOCAL_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024


def read_bounded_local_file(path: Path, total_bytes: int = 0) -> bytes:
    """Read a local file without allowing it to exceed attachment limits."""
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_LOCAL_ATTACHMENT_BYTES + 1)
    except OSError as err:
        raise HomeAssistantError(f"Unable to read `{path}`: {err}") from err

    size = len(content)
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
    return content
