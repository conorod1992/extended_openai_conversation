"""Residual coverage for file function safety helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.functions import file as file_fn


def test_read_text_bounded_detects_growth_after_stat(tmp_path, monkeypatch) -> None:
    path = tmp_path / "growing.txt"
    path.write_bytes(b"abcd")
    monkeypatch.setattr(file_fn, "FILE_READ_SIZE_LIMIT", 3)

    with pytest.raises(ValueError, match="File too large"):
        file_fn._read_text_bounded(path)


def test_read_text_bounded_detects_growth_during_read(tmp_path, monkeypatch) -> None:
    path = tmp_path / "growing.txt"
    path.write_bytes(b"abcd")
    monkeypatch.setattr(file_fn, "FILE_READ_SIZE_LIMIT", 3)
    monkeypatch.setattr(Path, "stat", lambda self: SimpleNamespace(st_size=3))

    with pytest.raises(ValueError, match="grew beyond the read limit"):
        file_fn._read_text_bounded(path)


def test_snapshot_rejects_initially_oversized_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "large.txt"
    path.write_bytes(b"abcd")
    monkeypatch.setattr(file_fn, "FILE_READ_SIZE_LIMIT", 3)

    with pytest.raises(ValueError, match="File too large"):
        file_fn._read_text_bounded_snapshot(path)


def test_snapshot_detects_growth_and_version_change(tmp_path, monkeypatch) -> None:
    path = tmp_path / "snapshot.txt"
    path.write_bytes(b"abcd")
    monkeypatch.setattr(file_fn, "FILE_READ_SIZE_LIMIT", 3)

    real_fstat = os.fstat
    calls = 0

    def fake_fstat(fd):
        nonlocal calls
        calls += 1
        stat_result = real_fstat(fd)
        if calls == 1:
            return SimpleNamespace(
                st_dev=stat_result.st_dev,
                st_ino=stat_result.st_ino,
                st_size=3,
                st_mtime_ns=stat_result.st_mtime_ns,
                st_ctime_ns=stat_result.st_ctime_ns,
            )
        return stat_result

    monkeypatch.setattr(file_fn.os, "fstat", fake_fstat)
    with pytest.raises(ValueError, match="grew beyond the read limit"):
        file_fn._read_text_bounded_snapshot(path)

    monkeypatch.setattr(file_fn, "FILE_READ_SIZE_LIMIT", 10)
    calls = 0

    def changed_fstat(fd):
        nonlocal calls
        calls += 1
        stat_result = real_fstat(fd)
        return SimpleNamespace(
            st_dev=stat_result.st_dev,
            st_ino=stat_result.st_ino,
            st_size=stat_result.st_size,
            st_mtime_ns=stat_result.st_mtime_ns + (1 if calls > 1 else 0),
            st_ctime_ns=stat_result.st_ctime_ns,
        )

    monkeypatch.setattr(file_fn.os, "fstat", changed_fstat)
    with pytest.raises(RuntimeError, match="changed while it was being read"):
        file_fn._read_text_bounded_snapshot(path)


def test_atomic_replace_if_unchanged_rejects_deleted_or_changed_path(tmp_path) -> None:
    path = tmp_path / "target.txt"
    path.write_text("old", encoding="utf-8")
    expected = file_fn._fingerprint(path.stat())

    path.unlink()
    with pytest.raises(RuntimeError, match="changed since it was read"):
        file_fn._atomic_replace_text_if_unchanged(path, "new", expected)

    path.write_text("different", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed since it was read"):
        file_fn._atomic_replace_text_if_unchanged(path, "new", expected)


class _Template:
    def __init__(self, source: str, rendered: str) -> None:
        self.template = source
        self._rendered = rendered

    def async_render(self, _arguments, parse_result=False):
        return self._rendered


class _SkillManager:
    def __init__(self, skill=None) -> None:
        self._skill = skill

    @asynccontextmanager
    async def async_skill_read(self):
        yield

    def get_skill(self, _name):
        return self._skill


async def test_skill_read_reports_missing_manager_and_skill(hass, monkeypatch) -> None:
    function = file_fn.ReadFileFunction()
    config = {"path": _Template("{{ extended_openai.skill_dir }}", "unused")}

    monkeypatch.setattr(file_fn.SkillManager, "get_loaded_instance", lambda: None)
    assert await function.execute(hass, config, {"name": "demo"}, None, None) == {
        "error": "Skill not found: demo"
    }

    monkeypatch.setattr(
        file_fn.SkillManager,
        "get_loaded_instance",
        lambda: _SkillManager(None),
    )
    assert await function.execute(hass, config, {"name": "demo"}, None, None) == {
        "error": "Skill not found: demo"
    }


async def test_skill_read_uses_skill_parent_as_only_allowed_directory(
    hass, tmp_path, monkeypatch
) -> None:
    skill_path = tmp_path / "skills" / "demo.md"
    skill_path.parent.mkdir()
    skill_path.write_text("skill body", encoding="utf-8")
    skill = SimpleNamespace(path=skill_path)
    function = file_fn.ReadFileFunction()
    config = {
        "path": _Template(
            "{{ extended_openai.skill_dir }}/demo.md",
            str(skill_path),
        )
    }
    monkeypatch.setattr(
        file_fn.SkillManager,
        "get_loaded_instance",
        lambda: _SkillManager(skill),
    )

    result = await function.execute(hass, config, {"name": "demo"}, None, None)

    assert result == {"content": "skill body", "size": len("skill body")}
