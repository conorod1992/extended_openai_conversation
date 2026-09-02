"""Regression tests for local-tool resource ceilings."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.const import (
    FILE_READ_SIZE_LIMIT,
    SHELL_OUTPUT_LIMIT,
)
from custom_components.extended_openai_conversation_responses.functions.bash import (
    _read_bounded_stream,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    _atomic_replace_text,
    _read_text_bounded,
)
from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    _execute_sqlite_query,
    _read_only_sqlite_uri,
)


async def test_bash_stream_drain_retains_only_configured_limit() -> None:
    """Large process output is drained without retaining the full stream in memory."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * (SHELL_OUTPUT_LIMIT * 3))
    reader.feed_eof()

    content, truncated = await _read_bounded_stream(reader)

    assert len(content) == SHELL_OUTPUT_LIMIT
    assert truncated is True


def _create_sqlite_database(tmp_path) -> str:
    path = tmp_path / "bounded.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample VALUES ('ok')")
        conn.commit()
    finally:
        conn.close()
    return _read_only_sqlite_uri(str(path))


def test_sqlite_query_deadline_interrupts_work(tmp_path) -> None:
    """SQLite progress handling interrupts CPU-heavy queries at the deadline."""
    db_url = _create_sqlite_database(tmp_path)
    query = """
        WITH RECURSIVE counter(value) AS (
            VALUES(0)
            UNION ALL
            SELECT value + 1 FROM counter WHERE value < 100000000
        )
        SELECT sum(value) AS total FROM counter
    """

    with pytest.raises(HomeAssistantError, match="execution deadline"):
        _execute_sqlite_query(
            db_url,
            query,
            True,
            1,
            timeout_seconds=0.0,
            max_result_bytes=1024,
        )


def test_sqlite_result_byte_ceiling_applies_to_single_rows(tmp_path) -> None:
    """A single very wide SQLite row cannot bypass the result-size ceiling."""
    db_url = _create_sqlite_database(tmp_path)

    with pytest.raises(HomeAssistantError, match="result-size limit"):
        _execute_sqlite_query(
            db_url,
            "SELECT hex(zeroblob(10000)) AS payload",
            True,
            1,
            timeout_seconds=5,
            max_result_bytes=1024,
        )


def test_sqlite_result_byte_ceiling_applies_while_rows_accumulate(tmp_path) -> None:
    """Multi-row queries stop before an oversized result list is retained."""
    db_url = _create_sqlite_database(tmp_path)

    with pytest.raises(HomeAssistantError, match="result-size limit"):
        _execute_sqlite_query(
            db_url,
            """
                WITH RECURSIVE counter(value) AS (
                    VALUES(1)
                    UNION ALL
                    SELECT value + 1 FROM counter WHERE value < 100
                )
                SELECT hex(zeroblob(100)) AS payload FROM counter
            """,
            False,
            1000,
            timeout_seconds=5,
            max_result_bytes=1024,
        )


def test_file_bounded_reader_rejects_oversized_content(tmp_path) -> None:
    """Edit/read helpers cannot allocate an arbitrarily large existing file."""
    target = tmp_path / "large.txt"
    target.write_bytes(b"x" * (FILE_READ_SIZE_LIMIT + 1))

    with pytest.raises(ValueError, match="File too large"):
        _read_text_bounded(target)


def test_atomic_edit_rejects_oversized_replacement_without_touching_file(tmp_path) -> None:
    """An edit that would exceed the file ceiling leaves the original untouched."""
    target = tmp_path / "edit.txt"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="would exceed the size limit"):
        _atomic_replace_text(target, "x" * (FILE_READ_SIZE_LIMIT + 1))

    assert target.read_text(encoding="utf-8") == "original"


def test_atomic_edit_failure_leaves_original_and_cleans_temp(tmp_path, monkeypatch) -> None:
    """Failure before os.replace cannot partially overwrite the target file."""
    target = tmp_path / "edit.txt"
    target.write_text("original", encoding="utf-8")

    def fail_replace(_source, _target) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.file.os.replace",
        fail_replace,
    )

    with pytest.raises(OSError, match="simulated replace failure"):
        _atomic_replace_text(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target]
