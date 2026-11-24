"""Focused coverage for Skill resource ceilings and bounded readers."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import skill_resource_limits as limits


def test_read_bounded_skill_text_reports_io_error(tmp_path: Path) -> None:
    """An unreadable/missing local Skill should surface a useful HA error."""
    path = tmp_path / "missing" / "SKILL.md"

    with pytest.raises(HomeAssistantError, match="Unable to read Skill metadata"):
        limits.read_bounded_skill_text(path)


def test_read_bounded_skill_text_rejects_oversize_and_invalid_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local metadata is bounded by bytes and must be valid UTF-8."""
    monkeypatch.setattr(limits, "MAX_SKILL_METADATA_BYTES", 4)
    path = tmp_path / "SKILL.md"

    path.write_bytes(b"abcde")
    with pytest.raises(HomeAssistantError, match="exceeds the 4-byte limit"):
        limits.read_bounded_skill_text(path)

    path.write_bytes(b"\xff")
    with pytest.raises(HomeAssistantError, match="is not valid UTF-8"):
        limits.read_bounded_skill_text(path)

    path.write_bytes("é".encode())
    assert limits.read_bounded_skill_text(path) == "é"
