"""Focused regressions for pre-allocation/read resource ceilings."""

from __future__ import annotations

import base64
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
)
import custom_components.extended_openai_conversation_responses.entity as entity_module
from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    _execute_sqlite_query,
    _set_sqlite_result_limit,
)
import custom_components.extended_openai_conversation_responses.services as services_module
import custom_components.extended_openai_conversation_responses.skill_resource_limits as skill_limits
import custom_components.extended_openai_conversation_responses.skills as skills_module
from custom_components.extended_openai_conversation_responses.skills import SkillManager


class _ChunkedContent:
    """Minimal aiohttp-style response body for bounded-reader tests."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


def _response(*chunks: bytes, content_length: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        content_length=content_length,
        content=_ChunkedContent(list(chunks)),
    )


def _make_empty_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.close()


def test_sqlite_installs_native_length_limit() -> None:
    """The configured result ceiling reaches SQLite before query execution."""
    conn = Mock()

    _set_sqlite_result_limit(conn, 4096)

    conn.setlimit.assert_called_once_with(sqlite3.SQLITE_LIMIT_LENGTH, 4096)


@pytest.mark.parametrize("expression", ["zeroblob(4096)", "hex(zeroblob(4096))"])
def test_sqlite_rejects_generated_large_values_before_materialization(
    tmp_path: Path, expression: str
) -> None:
    """SQLite itself rejects oversized generated BLOB/scalar values."""
    db_path = tmp_path / "limits.db"
    _make_empty_db(db_path)
    db_url = f"{db_path.resolve().as_uri()}?mode=ro"

    with pytest.raises(HomeAssistantError, match="result-size limit"):
        _execute_sqlite_query(
            db_url,
            f"SELECT {expression} AS payload",
            True,
            10,
            max_result_bytes=1024,
        )


async def test_bounded_http_reader_rejects_declared_and_streamed_overflow() -> None:
    """Remote bodies are capped both before and while bytes arrive."""
    with pytest.raises(HomeAssistantError, match="download limit"):
        await skill_limits.async_read_bounded_response(
            _response(b"unused", content_length=11), 10, "Skill file"
        )

    with pytest.raises(HomeAssistantError, match="download limit"):
        await skill_limits.async_read_bounded_response(
            _response(b"123456", b"78901"), 10, "Skill file"
        )


async def test_bounded_json_reader_rejects_large_listing(monkeypatch) -> None:
    """GitHub directory JSON cannot be materialized without a response ceiling."""
    monkeypatch.setattr(skill_limits, "MAX_SKILL_API_RESPONSE_BYTES", 8)

    with pytest.raises(HomeAssistantError, match="download limit"):
        await skill_limits.async_read_bounded_json(
            _response(b'[{"name":', b'"too-large"}]'), "Skill listing"
        )


def test_skill_download_budget_caps_depth_count_file_and_total(monkeypatch) -> None:
    """Every recursive download dimension has an independent hard ceiling."""
    monkeypatch.setattr(skill_limits, "MAX_SKILL_DOWNLOAD_DEPTH", 1)
    monkeypatch.setattr(skill_limits, "MAX_SKILL_DOWNLOAD_FILES", 2)
    monkeypatch.setattr(skill_limits, "MAX_SKILL_FILE_BYTES", 6)
    monkeypatch.setattr(skill_limits, "MAX_SKILL_TOTAL_BYTES", 10)

    budget = skill_limits.SkillDownloadBudget()
    budget.check_directory(1)
    with pytest.raises(HomeAssistantError, match="directory depth"):
        budget.check_directory(2)

    with pytest.raises(HomeAssistantError, match="per-file limit"):
        budget.check_file("oversized.bin", 7)

    assert budget.check_file("first.bin", 6) == 6
    budget.record_file("first.bin", 6)
    assert budget.check_file("second.bin", 4) == 4
    budget.record_file("second.bin", 4)

    with pytest.raises(HomeAssistantError, match="file count|combined download"):
        budget.check_file("third.bin", 1)


def test_local_skill_discovery_caps_count_and_metadata_size(
    tmp_path: Path, monkeypatch
) -> None:
    """Local discovery cannot ingest unlimited Skills or oversized SKILL.md files."""
    monkeypatch.setattr(skills_module, "MAX_DISCOVERED_SKILLS", 2)
    manager = SkillManager.__new__(SkillManager)

    for index in range(3):
        skill_dir = tmp_path / f"skill-{index}"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\ndescription: Skill {index}\n---\nbody\n", encoding="utf-8"
        )

    discovered = manager._load_skills_from_dir_sync(tmp_path)
    assert len(discovered) == 2

    oversized_root = tmp_path / "oversized-root"
    oversized_root.mkdir()
    oversized_dir = oversized_root / "large"
    oversized_dir.mkdir()
    (oversized_dir / "SKILL.md").write_bytes(b"x" * 33)
    monkeypatch.setattr(skill_limits, "MAX_SKILL_METADATA_BYTES", 32)

    assert manager._load_skills_from_dir_sync(oversized_root) == []


async def test_chat_attachment_aggregates_bytes_from_bounded_read(
    hass, tmp_path: Path, monkeypatch
) -> None:
    """Chat aggregation uses the bytes actually read, not an earlier path size."""
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    totals: list[int] = []

    def bounded_snapshot(_path: Path, total_bytes: int = 0) -> bytes:
        totals.append(total_bytes)
        if total_bytes >= 6:
            raise HomeAssistantError("actual aggregate limit reached")
        return b"123456"

    monkeypatch.setattr(entity_module, "read_bounded_local_file", bounded_snapshot)
    attachments = [
        conversation.Attachment(
            media_content_id=f"media-source://camera/{index}",
            mime_type="image/png",
            path=path,
        )
        for index, path in enumerate((first, second))
    ]
    chat_log = conversation.ChatLog(hass, "conversation-id")
    chat_log.async_add_user_content(
        conversation.UserContent(content="inspect", attachments=attachments)
    )
    messages = entity_module._convert_content_to_responses_param(chat_log.content)
    entity = entity_module.ExtendedOpenAIBaseLLMEntity.__new__(
        entity_module.ExtendedOpenAIBaseLLMEntity
    )
    entity.hass = hass

    with pytest.raises(HomeAssistantError, match="actual aggregate"):
        await entity._async_add_attachments(chat_log, messages, API_MODE_RESPONSES)

    assert totals == [0, 6]


def test_query_image_uses_bounded_read_snapshot(hass, tmp_path: Path, monkeypatch) -> None:
    """Image service encodes and aggregates the exact bounded-read bytes."""
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    totals: list[int] = []

    def bounded_snapshot(_path: Path, total_bytes: int = 0) -> bytes:
        totals.append(total_bytes)
        return b"123456"

    monkeypatch.setattr(services_module, "read_bounded_local_file", bounded_snapshot)
    hass.config.is_allowed_path = MagicMock(return_value=True)

    prepared = services_module.prepare_image_params(
        hass, [{"url": str(first)}, {"url": str(second)}]
    )

    assert totals == [0, 6]
    encoded = prepared[0]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"123456"
