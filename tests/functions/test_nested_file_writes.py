"""Authorised write_file paths may create parents; reads and edits must not."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_WORKING_DIRECTORY,
    FILE_READ_SIZE_LIMIT,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    EditFileFunction,
    WriteFileFunction,
    _atomic_replace_text,
)


async def test_write_file_creates_nested_parents_and_atomically_overwrites(
    hass, tmp_path: Path, llm_context, exposed_entities
) -> None:
    tool = WriteFileFunction()
    config = tool.validate_schema(
        {"type": "write_file", "path": "notes/daily/today.md", "content": "First\\n"}
    )
    target = tmp_path / DEFAULT_WORKING_DIRECTORY / "notes/daily/today.md"
    assert not target.parent.exists()

    result = await tool.execute(hass, config, {}, llm_context, exposed_entities)
    assert result["success"] is True
    assert result["bytes_written"] == len("First\\n".encode())
    assert target.read_text() == "First\\n"
    assert not list(target.parent.glob(".*.tmp"))

    replacement = tool.validate_schema(
        {"type": "write_file", "path": "notes/daily/today.md", "content": "Updated"}
    )
    assert (await tool.execute(hass, replacement, {}, llm_context, exposed_entities))[
        "success"
    ]
    assert target.read_text() == "Updated"


async def test_write_file_does_not_create_denied_parent(
    hass, tmp_path: Path, llm_context, exposed_entities
) -> None:
    tool = WriteFileFunction()
    config = tool.validate_schema(
        {
            "type": "write_file",
            "path": "../outside/new/secret.md",
            "content": "Should not exist",
        }
    )
    result = await tool.execute(hass, config, {}, llm_context, exposed_entities)
    assert "error" in result
    assert not (tmp_path / "outside").exists()


async def test_edit_file_does_not_create_missing_parents(
    hass, tmp_path: Path, llm_context, exposed_entities
) -> None:
    tool = EditFileFunction()
    config = tool.validate_schema(
        {
            "type": "edit_file",
            "path": "new/nested/old.md",
            "old_text": "original",
            "new_text": "replacement",
        }
    )
    result = await tool.execute(hass, config, {}, llm_context, exposed_entities)
    assert "error" in result
    assert not (tmp_path / DEFAULT_WORKING_DIRECTORY / "new").exists()


def test_oversized_write_does_not_create_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "notes" / "daily" / "today.md"
    with pytest.raises(ValueError, match="size limit"):
        _atomic_replace_text(
            path, "x" * (FILE_READ_SIZE_LIMIT + 1), create_parents=True
        )
    assert not path.parent.parent.exists()
