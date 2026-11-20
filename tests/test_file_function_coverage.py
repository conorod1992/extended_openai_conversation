"""Residual coverage for file function safety helpers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.functions import (
    file as file_fn,
)
from homeassistant.exceptions import HomeAssistantError
from tests.helpers import prepare_function_tool_from_yaml


def test_read_text_bounded_detects_growth_after_stat(tmp_path, monkeypatch) -> None:
    path = tmp_path / "growing.txt"
    path.write_bytes(b"abcd")
    monkeypatch.setattr(file_fn, "FILE_READ_SIZE_LIMIT", 3)

    with pytest.raises(ValueError, match="File too large"):
        file_fn._read_text_bounded(path)


@pytest.mark.parametrize("operation", ["read", "write", "edit"])
async def test_file_tool_path_io_runs_off_event_loop(hass, monkeypatch, operation):
    path = Path(hass.config.config_dir, file_fn.DEFAULT_WORKING_DIRECTORY, "notes.txt")
    path.write_text("old text", encoding="utf-8")
    real_stat = Path.stat
    real_resolve = Path.resolve
    owner = get_ident()
    calls = []

    def stat(self, *args, **kwargs):
        if self == path:
            calls.append(("stat", get_ident()))
        return real_stat(self, *args, **kwargs)

    def resolve(self, *args, **kwargs):
        if self == path:
            calls.append(("resolve", get_ident()))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "resolve", resolve)
    hass.async_add_executor_job.side_effect = asyncio.to_thread
    tool = prepare_function_tool_from_yaml(f"{operation}_file_example.yaml", 0)
    runner = {
        "read": file_fn.ReadFileFunction,
        "write": file_fn.WriteFileFunction,
        "edit": file_fn.EditFileFunction,
    }[operation]()
    result = await runner.execute(
        hass,
        tool["function"],
        {"filename": path.name, "content": "new", "old_text": "old", "new_text": "new"},
        None,
        [],
    )
    assert result.get("success", result.get("content") == "old text"), result
    assert {kind for kind, _ in calls} == {"resolve", "stat"}
    assert all(thread != owner for _, thread in calls)


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


async def test_skill_read_reports_missing_manager(hass, monkeypatch) -> None:
    from custom_components.extended_openai_conversation_responses.template import (
        ExtendedOpenAITemplateManager,
    )
    from homeassistant.helpers.template import Template

    function = file_fn.ReadFileFunction()
    monkeypatch.setattr(file_fn.SkillManager, "_instance", None)
    monkeypatch.setitem(hass.data["template.environment"].globals, "extended_openai", ExtendedOpenAITemplateManager(hass)._extended_openai)
    config = {"path": Template("{{ extended_openai.skill_dir('demo') }}/SKILL.md", hass)}
    result = await function.execute(hass, config, {}, None, None)
    assert "SkillManager not initialized" in result["error"]


async def test_edit_settlement_defers_cancellation_until_writer_finishes() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def write() -> int:
        started.set()
        await release.wait()
        return 1

    task = asyncio.create_task(file_fn._async_settle_native_edit(write()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_non_skill_path_template_errors_remain_visible(hass) -> None:
    class BrokenTemplate:
        template = "ordinary path"

        def async_render(self, *_args, **_kwargs):
            raise HomeAssistantError("template failed")

    function = file_fn.ReadFileFunction()
    config = {"path": BrokenTemplate()}

    with pytest.raises(HomeAssistantError, match="template failed"):
        await function.execute(hass, config, {}, None, None)
