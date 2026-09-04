"""Resource ceilings for local Skill discovery and remote Skill downloads."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from homeassistant.exceptions import HomeAssistantError

# These ceilings are intentionally generous so ordinary Skills are unaffected while
# malformed or unexpectedly large trees cannot consume unbounded memory/disk/CPU.
MAX_SKILL_DISCOVERY_ENTRIES = 4096
MAX_DISCOVERED_SKILLS = 512
MAX_SKILL_METADATA_BYTES = 2 * 1024 * 1024
MAX_SKILL_DOWNLOAD_DEPTH = 16
MAX_SKILL_DOWNLOAD_FILES = 1024
MAX_SKILL_FILE_BYTES = 16 * 1024 * 1024
MAX_SKILL_TOTAL_BYTES = 128 * 1024 * 1024
MAX_SKILL_API_RESPONSE_BYTES = 4 * 1024 * 1024
_HTTP_CHUNK_BYTES = 64 * 1024


def read_bounded_skill_text(path: Path) -> str:
    """Read one local SKILL.md without permitting an unbounded allocation."""
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_SKILL_METADATA_BYTES + 1)
    except OSError as err:
        raise HomeAssistantError(
            f"Unable to read Skill metadata `{path}`: {err}"
        ) from err

    if len(content) > MAX_SKILL_METADATA_BYTES:
        raise HomeAssistantError(
            f"Skill metadata `{path}` exceeds the {MAX_SKILL_METADATA_BYTES}-byte limit"
        )
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as err:
        raise HomeAssistantError(f"Skill metadata `{path}` is not valid UTF-8") from err


async def async_read_bounded_response(
    response: Any, max_bytes: int, description: str
) -> bytes:
    """Read an HTTP response incrementally and fail before it can grow unbounded."""
    content_length = getattr(response, "content_length", None)
    if isinstance(content_length, int) and content_length > max_bytes:
        raise HomeAssistantError(
            f"{description} exceeds the {max_bytes}-byte download limit"
        )

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(_HTTP_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise HomeAssistantError(
                f"{description} exceeds the {max_bytes}-byte download limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def async_read_bounded_json(response: Any, description: str) -> Any:
    """Read and decode a bounded GitHub API response."""
    content = await async_read_bounded_response(
        response, MAX_SKILL_API_RESPONSE_BYTES, description
    )
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise HomeAssistantError(f"{description} is not valid JSON") from err


@dataclass(slots=True)
class SkillDownloadBudget:
    """Track one staged Skill download's depth, file count, and actual bytes."""

    files: int = 0
    total_bytes: int = 0

    def check_directory(self, depth: int) -> None:
        """Reject recursion beyond the supported Skill tree depth."""
        if depth > MAX_SKILL_DOWNLOAD_DEPTH:
            raise HomeAssistantError(
                "Downloaded Skill exceeds the maximum directory depth of "
                f"{MAX_SKILL_DOWNLOAD_DEPTH}"
            )

    def check_file(self, path: str, declared_size: Any = None) -> int:
        """Return the maximum safe bytes to read for the next file."""
        if self.files >= MAX_SKILL_DOWNLOAD_FILES:
            raise HomeAssistantError(
                "Downloaded Skill exceeds the maximum file count of "
                f"{MAX_SKILL_DOWNLOAD_FILES}"
            )
        remaining = MAX_SKILL_TOTAL_BYTES - self.total_bytes
        if remaining <= 0:
            raise HomeAssistantError(
                "Downloaded Skill exceeds the combined download limit of "
                f"{MAX_SKILL_TOTAL_BYTES} bytes"
            )
        max_bytes = min(MAX_SKILL_FILE_BYTES, remaining)
        if isinstance(declared_size, int) and declared_size > max_bytes:
            if declared_size > MAX_SKILL_FILE_BYTES:
                raise HomeAssistantError(
                    f"Downloaded Skill file `{path}` exceeds the per-file limit of "
                    f"{MAX_SKILL_FILE_BYTES} bytes"
                )
            raise HomeAssistantError(
                "Downloaded Skill exceeds the combined download limit of "
                f"{MAX_SKILL_TOTAL_BYTES} bytes"
            )
        return max_bytes

    def record_file(self, path: str, size: int) -> None:
        """Commit one successfully read file using its actual byte count."""
        if size > MAX_SKILL_FILE_BYTES:
            raise HomeAssistantError(
                f"Downloaded Skill file `{path}` exceeds the per-file limit of "
                f"{MAX_SKILL_FILE_BYTES} bytes"
            )
        if self.total_bytes + size > MAX_SKILL_TOTAL_BYTES:
            raise HomeAssistantError(
                "Downloaded Skill exceeds the combined download limit of "
                f"{MAX_SKILL_TOTAL_BYTES} bytes"
            )
        if self.files >= MAX_SKILL_DOWNLOAD_FILES:
            raise HomeAssistantError(
                "Downloaded Skill exceeds the maximum file count of "
                f"{MAX_SKILL_DOWNLOAD_FILES}"
            )
        self.files += 1
        self.total_bytes += size
