"""Focused residual coverage for the SQLite function."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any
from urllib import parse

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    sqlite as sqlite_module,
)
from homeassistant.exceptions import HomeAssistantError


def _make_db(tmp_path: Path) -> Path:
    path = tmp_path / "test.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO items(name) VALUES (?)",
            [("one",), ("two",), ("three",)],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_read_only_authorizer_denies_mutation_and_allows_reads() -> None:
    assert (
        sqlite_module._read_only_authorizer(
            sqlite3.SQLITE_INSERT, None, None, None, None
        )
        == sqlite3.SQLITE_DENY
    )
    assert (
        sqlite_module._read_only_authorizer(
            sqlite3.SQLITE_SELECT, None, None, None, None
        )
        == sqlite3.SQLITE_OK
    )


def test_read_only_uri_validation_and_mode_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(HomeAssistantError, match="in-memory"):
        sqlite_module._read_only_sqlite_uri(":memory:")

    # Non-file strings are valid filesystem paths, so force Path.as_uri() to
    # return a URI with an unsupported scheme to exercise the scheme guard.
    with monkeypatch.context() as patch:
        patch.setattr(
            sqlite_module.Path,
            "as_uri",
            lambda _path: "https://example.test/db.sqlite",
        )
        with pytest.raises(HomeAssistantError, match="filesystem path or file: URI"):
            sqlite_module._read_only_sqlite_uri("db.sqlite")

    path = tmp_path / "db.sqlite"
    uri = sqlite_module._read_only_sqlite_uri(str(path))
    parts = parse.urlsplit(uri)
    assert parts.scheme == "file"
    assert parse.parse_qs(parts.query)["mode"] == ["ro"]

    overridden = sqlite_module._read_only_sqlite_uri(
        f"{path.resolve().as_uri()}?cache=shared&mode=rw"
    )
    params = parse.parse_qs(parse.urlsplit(overridden).query)
    assert params["cache"] == ["shared"]
    assert params["mode"] == ["ro"]


def test_execute_query_single_empty_and_multi_row_limit(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    db_url = sqlite_module._read_only_sqlite_uri(str(db_path))

    assert sqlite_module._execute_sqlite_query(
        db_url, "SELECT name FROM items WHERE id = -1", True, 10
    ) == {}

    assert sqlite_module._execute_sqlite_query(
        db_url, "SELECT name FROM items WHERE id = 1", True, 10
    ) == {"name": "one"}

    with pytest.raises(HomeAssistantError, match="more than 2 rows"):
        sqlite_module._execute_sqlite_query(
            db_url, "SELECT id, name FROM items ORDER BY id", False, 2
        )


def test_execute_query_enforces_estimated_result_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _make_db(tmp_path)
    db_url = sqlite_module._read_only_sqlite_uri(str(db_path))
    monkeypatch.setattr(sqlite_module, "_set_sqlite_result_limit", lambda *_: None)

    with pytest.raises(HomeAssistantError, match="result-size limit of 8 bytes"):
        sqlite_module._execute_sqlite_query(
            db_url,
            "SELECT name FROM items WHERE id = 1",
            True,
            10,
            max_result_bytes=8,
        )

    with pytest.raises(HomeAssistantError, match="result-size limit of 20 bytes"):
        sqlite_module._execute_sqlite_query(
            db_url,
            "SELECT id, name FROM items ORDER BY id",
            False,
            10,
            max_result_bytes=20,
        )


class _FakeConnection:
    def __init__(self, execute_error: BaseException, *, invoke_progress: bool = False) -> None:
        self.execute_error = execute_error
        self.invoke_progress = invoke_progress
        self.progress_handler: Any = None
        self.closed = False

    def setlimit(self, *_args: Any) -> None:
        return None

    def execute(self, query: str) -> Any:
        if query == "PRAGMA query_only = ON":
            return None
        if self.invoke_progress and self.progress_handler is not None:
            self.progress_handler()
        raise self.execute_error

    def set_authorizer(self, _authorizer: Any) -> None:
        return None

    def set_progress_handler(self, handler: Any, _steps: int) -> None:
        self.progress_handler = handler

    def close(self) -> None:
        self.closed = True


def test_execute_query_translates_sqlite_too_big(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(sqlite3.DataError("string or blob too big"))
    monkeypatch.setattr(sqlite_module.sqlite3, "connect", lambda *_a, **_k: conn)

    with pytest.raises(HomeAssistantError, match="result-size limit of 123 bytes"):
        sqlite_module._execute_sqlite_query(
            "file:test.sqlite?mode=ro", "SELECT 1", False, 1, max_result_bytes=123
        )

    assert conn.closed is True
    assert conn.progress_handler is None


def test_execute_query_reraises_unrelated_data_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(sqlite3.DataError("other data error"))
    monkeypatch.setattr(sqlite_module.sqlite3, "connect", lambda *_a, **_k: conn)

    with pytest.raises(sqlite3.DataError, match="other data error"):
        sqlite_module._execute_sqlite_query(
            "file:test.sqlite?mode=ro", "SELECT 1", False, 1
        )


def test_execute_query_translates_deadline_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(
        sqlite3.OperationalError("interrupted"), invoke_progress=True
    )
    monkeypatch.setattr(sqlite_module.sqlite3, "connect", lambda *_a, **_k: conn)
    times = iter((10.0, 20.0))
    # sqlite_module.time is the process-wide time module. Keep the replacement
    # safe for framework cleanup that may also call monotonic before restoration.
    monkeypatch.setattr(sqlite_module.time, "monotonic", lambda: next(times, 20.0))

    with pytest.raises(HomeAssistantError, match="execution deadline of 5 seconds"):
        sqlite_module._execute_sqlite_query(
            "file:test.sqlite?mode=ro", "SELECT 1", False, 1, timeout_seconds=5
        )


def test_sqlite_function_exposure_helpers_and_raise() -> None:
    function = sqlite_module.SqliteFunction()
    exposed = [{"entity_id": "light.kitchen"}, {"entity_id": "sensor.temp"}]

    assert function.is_exposed("light.kitchen", exposed) is True
    assert function.is_exposed("light.bedroom", exposed) is False
    assert function.is_exposed_entity_in_query(
        "SELECT * FROM states WHERE entity_id = 'sensor.temp'", exposed
    ) is True
    assert function.is_exposed_entity_in_query("SELECT 1", exposed) is False

    with pytest.raises(HomeAssistantError, match="custom failure"):
        function.raise_error("custom failure")


def test_default_url_is_read_only(tmp_path: Path) -> None:
    function = sqlite_module.SqliteFunction()
    hass = SimpleNamespace(config=SimpleNamespace(config_dir=str(tmp_path)))

    uri = function.get_default_db_url(hass)
    params = parse.parse_qs(parse.urlsplit(uri).query)
    assert params["mode"] == ["ro"]


@pytest.mark.asyncio
async def test_execute_translates_executor_sqlite_errors(
    hass: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    function = sqlite_module.SqliteFunction()
    db_path = _make_db(tmp_path)

    async def fail_executor(*_args: Any) -> Any:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(hass, "async_add_executor_job", fail_executor)

    with pytest.raises(HomeAssistantError, match="SQLite query failed: database unavailable"):
        await function.execute(
            hass,
            {"db_url": str(db_path), "query": "SELECT 1"},
            {},
            None,
            [],
        )
