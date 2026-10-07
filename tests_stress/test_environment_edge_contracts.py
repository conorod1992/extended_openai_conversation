"""Concrete environment edge contracts not covered by deployment matrices."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import time
import unicodedata

import pytest

from custom_components.extended_openai_conversation_responses.functions.file import (
    ReadFileFunction,
    _atomic_replace_text,
    _get_edit_lock,
    _read_text_bounded,
)
from custom_components.extended_openai_conversation_responses.functions.sqlite import (
    _execute_sqlite_query,
    _read_only_sqlite_uri,
)
from homeassistant.exceptions import HomeAssistantError
from tests_stress.conftest import record


def test_unicode_filename_atomic_roundtrip_and_path_identity(hass, tmp_path, stress_trace):
    """Native file helpers retain Unicode paths and lock by resolved filesystem identity."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    composed = "café-資料.txt"
    path = workspace / composed
    payload = "Unicode payload: café — 資料 — 🦕"

    assert _atomic_replace_text(path, payload) == len(payload.encode("utf-8"))
    assert _read_text_bounded(path) == payload

    reader = ReadFileFunction()
    resolved = reader._resolve_path(hass, str(path), [str(workspace)])
    assert resolved == path.resolve()

    # A symlink is a real-world alternate spelling of the same target. Resolution
    # must collapse it before the file-edit lock is selected, preventing two writers
    # from bypassing serialization through path aliases.
    alias = workspace / "alias.txt"
    try:
        alias.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("filesystem does not permit symlink creation")
    alias_resolved = reader._resolve_path(hass, str(alias), [str(workspace)])
    assert alias_resolved == resolved
    assert _get_edit_lock(hass, alias_resolved) is _get_edit_lock(hass, resolved)

    # Where the host filesystem distinguishes NFC/NFD names, both remain usable
    # independent files rather than being silently conflated by EOAI.
    decomposed_name = unicodedata.normalize("NFD", composed)
    if decomposed_name != composed:
        decomposed = workspace / decomposed_name
        _atomic_replace_text(decomposed, "decomposed")
        if decomposed.resolve() != resolved:
            assert _read_text_bounded(decomposed) == "decomposed"
            assert _get_edit_lock(hass, decomposed.resolve()) is not _get_edit_lock(
                hass, resolved
            )

    record(
        stress_trace,
        "filesystem_identity_contract",
        unicode_roundtrip=True,
        symlink_alias_serialized=True,
        normcase=os.path.normcase(str(resolved)),
    )


def test_sqlite_lock_deadline_then_recovery(tmp_path, stress_trace):
    """A native database lock consumes only the configured deadline and then recovers."""
    db = tmp_path / "locked.db"
    owner = sqlite3.connect(db)
    try:
        owner.execute("CREATE TABLE sample (value TEXT)")
        owner.execute("INSERT INTO sample VALUES ('healthy')")
        owner.commit()
        owner.execute("BEGIN EXCLUSIVE")

        uri = _read_only_sqlite_uri(str(db))
        started = time.monotonic()
        with pytest.raises(HomeAssistantError, match="execution deadline"):
            _execute_sqlite_query(uri, "SELECT value FROM sample", True, 10, 0.1)
        elapsed = time.monotonic() - started
        # Leave scheduler/CI noise room while still proving a native lock cannot
        # consume SQLite's usual multi-second default busy timeout.
        assert elapsed < 1.5

        owner.rollback()
        result = _execute_sqlite_query(uri, "SELECT value FROM sample", True, 10, 1.0)
        assert result == {"value": "healthy"}
    finally:
        owner.close()

    record(
        stress_trace,
        "sqlite_environment_contract",
        lock_deadline=True,
        recovered_after_unlock=True,
    )
