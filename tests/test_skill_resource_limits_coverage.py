"""Focused coverage for Skill resource ceilings and bounded readers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses import skill_resource_limits as limits


class _ChunkedContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.requested_chunk_size: int | None = None

    async def iter_chunked(self, chunk_size: int):
        self.requested_chunk_size = chunk_size
        for chunk in self._chunks:
            yield chunk


class _Response:
    def __init__(self, chunks: list[bytes], content_length: Any = None) -> None:
        self.content_length = content_length
        self.content = _ChunkedContent(chunks)


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


@pytest.mark.asyncio
async def test_bounded_response_rejects_declared_and_streamed_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both trustworthy headers and actual streamed bytes enforce the ceiling."""
    monkeypatch.setattr(limits, "_HTTP_CHUNK_BYTES", 3)

    declared = _Response([b"ignored"], content_length=6)
    with pytest.raises(HomeAssistantError, match="payload exceeds the 5-byte download limit"):
        await limits.async_read_bounded_response(declared, 5, "payload")

    streamed = _Response([b"abc", b"def"], content_length=None)
    with pytest.raises(HomeAssistantError, match="payload exceeds the 5-byte download limit"):
        await limits.async_read_bounded_response(streamed, 5, "payload")
    assert streamed.content.requested_chunk_size == 3


@pytest.mark.asyncio
async def test_bounded_response_accepts_exact_limit_and_ignores_non_int_length() -> None:
    """The exact byte boundary is accepted; non-integer length metadata is advisory."""
    response = _Response([b"ab", b"cde"], content_length="999")

    assert await limits.async_read_bounded_response(response, 5, "payload") == b"abcde"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"not-json", b"\xff"])
async def test_bounded_json_wraps_invalid_payloads(payload: bytes) -> None:
    """Malformed JSON and invalid UTF-8 become stable Home Assistant errors."""
    response = _Response([payload])

    with pytest.raises(HomeAssistantError, match="GitHub response is not valid JSON"):
        await limits.async_read_bounded_json(response, "GitHub response")


@pytest.mark.asyncio
async def test_bounded_json_returns_decoded_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid API payloads are decoded after bounded reading."""
    monkeypatch.setattr(limits, "MAX_SKILL_API_RESPONSE_BYTES", 32)
    response = _Response([b'{"ok":', b" true}"])

    assert await limits.async_read_bounded_json(response, "GitHub response") == {"ok": True}


def test_skill_download_budget_directory_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured maximum directory depth is inclusive."""
    monkeypatch.setattr(limits, "MAX_SKILL_DOWNLOAD_DEPTH", 2)
    budget = limits.SkillDownloadBudget()

    budget.check_directory(2)
    with pytest.raises(HomeAssistantError, match="maximum directory depth of 2"):
        budget.check_directory(3)


def test_skill_download_budget_check_file_enforces_file_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No additional read is permitted once the file-count budget is exhausted."""
    monkeypatch.setattr(limits, "MAX_SKILL_DOWNLOAD_FILES", 2)
    budget = limits.SkillDownloadBudget(files=2)

    with pytest.raises(HomeAssistantError, match="maximum file count of 2"):
        budget.check_file("third.txt")


def test_skill_download_budget_check_file_enforces_total_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completely consumed aggregate budget rejects another file before reading."""
    monkeypatch.setattr(limits, "MAX_SKILL_TOTAL_BYTES", 10)
    budget = limits.SkillDownloadBudget(total_bytes=10)

    with pytest.raises(HomeAssistantError, match="combined download limit of 10 bytes"):
        budget.check_file("next.txt")


def test_skill_download_budget_declared_size_distinguishes_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declared sizes identify whether the per-file or aggregate ceiling is hit."""
    monkeypatch.setattr(limits, "MAX_SKILL_FILE_BYTES", 6)
    monkeypatch.setattr(limits, "MAX_SKILL_TOTAL_BYTES", 10)

    fresh = limits.SkillDownloadBudget()
    with pytest.raises(HomeAssistantError, match="per-file limit of 6 bytes"):
        fresh.check_file("large.bin", declared_size=7)

    nearly_full = limits.SkillDownloadBudget(total_bytes=6)
    with pytest.raises(HomeAssistantError, match="combined download limit of 10 bytes"):
        nearly_full.check_file("remaining.bin", declared_size=5)

    assert nearly_full.check_file("exact.bin", declared_size=4) == 4
    assert nearly_full.check_file("unknown.bin", declared_size="4") == 4


def test_skill_download_budget_record_file_enforces_all_ceilings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual downloaded bytes are independently checked before budget mutation."""
    monkeypatch.setattr(limits, "MAX_SKILL_FILE_BYTES", 6)
    monkeypatch.setattr(limits, "MAX_SKILL_TOTAL_BYTES", 10)
    monkeypatch.setattr(limits, "MAX_SKILL_DOWNLOAD_FILES", 2)

    budget = limits.SkillDownloadBudget()
    with pytest.raises(HomeAssistantError, match="per-file limit of 6 bytes"):
        budget.record_file("large.bin", 7)
    assert (budget.files, budget.total_bytes) == (0, 0)

    budget = limits.SkillDownloadBudget(total_bytes=5)
    with pytest.raises(HomeAssistantError, match="combined download limit of 10 bytes"):
        budget.record_file("overflow.bin", 6)
    assert (budget.files, budget.total_bytes) == (0, 5)

    budget = limits.SkillDownloadBudget(files=2)
    with pytest.raises(HomeAssistantError, match="maximum file count of 2"):
        budget.record_file("third.bin", 1)
    assert (budget.files, budget.total_bytes) == (2, 0)

    budget = limits.SkillDownloadBudget(files=1, total_bytes=4)
    budget.record_file("ok.bin", 6)
    assert (budget.files, budget.total_bytes) == (2, 10)
