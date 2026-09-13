"""Tests for shared model-facing resource limits."""

from pathlib import Path

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.resource_limits import (
    MAX_TOTAL_ATTACHMENT_BYTES,
    bounded_local_file_size,
    read_bounded_local_file,
)


def test_bounded_local_file_size_returns_size(tmp_path: Path) -> None:
    """Return the file size when it is within all configured limits."""
    path = tmp_path / "attachment.bin"
    path.write_bytes(b"test")

    assert bounded_local_file_size(path) == 4


def test_bounded_local_file_size_enforces_combined_limit(tmp_path: Path) -> None:
    """Reject a file that would push the request over the aggregate limit."""
    path = tmp_path / "attachment.bin"
    path.write_bytes(b"x")

    with pytest.raises(HomeAssistantError, match="combined request limit"):
        bounded_local_file_size(path, total_bytes=MAX_TOTAL_ATTACHMENT_BYTES)


def test_bounded_local_file_size_wraps_stat_error(tmp_path: Path) -> None:
    """Expose filesystem stat failures as Home Assistant errors."""
    path = tmp_path / "missing.bin"

    with pytest.raises(HomeAssistantError, match="Unable to read") as exc_info:
        bounded_local_file_size(path)

    assert isinstance(exc_info.value.__cause__, OSError)


def test_read_bounded_local_file_returns_content(tmp_path: Path) -> None:
    """Read and return content that is within the configured limits."""
    path = tmp_path / "attachment.bin"
    content = b"bounded attachment"
    path.write_bytes(content)

    assert read_bounded_local_file(path) == content


def test_read_bounded_local_file_wraps_open_error(tmp_path: Path) -> None:
    """Expose filesystem open failures as Home Assistant errors."""
    path = tmp_path / "missing.bin"

    with pytest.raises(HomeAssistantError, match="Unable to read") as exc_info:
        read_bounded_local_file(path)

    assert isinstance(exc_info.value.__cause__, OSError)
