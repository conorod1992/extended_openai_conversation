"""Regression tests for durable Store privacy and bounded atomic file writes."""

from __future__ import annotations

from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    persistence_hardening as ph,
)
from custom_components.extended_openai_conversation_responses.const import (
    FILE_READ_SIZE_LIMIT,
)
from custom_components.extended_openai_conversation_responses.delayed_tools import (
    DelayedToolManager,
)
from custom_components.extended_openai_conversation_responses.functions.file import (
    _atomic_replace_text,
)
from custom_components.extended_openai_conversation_responses.persistence_hardening import (
    _async_repair_private_store_mode,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    STORAGE_VERSION,
    RequestRuleStore,
)
from homeassistant.helpers.storage import Store


def test_atomic_write_rejects_oversized_content_without_touching_file(
    tmp_path: Path,
) -> None:
    """The shared write/edit helper rejects oversized output before replacement."""
    path = tmp_path / "bounded.txt"
    path.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="size limit"):
        _atomic_replace_text(path, "x" * (FILE_READ_SIZE_LIMIT + 1))

    assert path.read_text(encoding="utf-8") == "original"


def test_atomic_write_preserves_existing_mode(tmp_path: Path) -> None:
    """Replacing an existing tool file keeps its prior permission mode."""
    path = tmp_path / "mode.txt"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o640)

    assert _atomic_replace_text(path, "new") == 3

    assert path.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_atomic_write_failure_leaves_existing_file_intact(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed final rename cannot leave a partially-written destination."""
    path = tmp_path / "atomic.txt"
    path.write_text("old", encoding="utf-8")

    def fail_replace(_source, _destination) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.functions.file.os.replace",
        fail_replace,
    )

    with pytest.raises(OSError, match="replace failed"):
        _atomic_replace_text(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".atomic.txt.*.tmp")) == []


async def test_private_store_mode_repair_preserves_existing_file_contents(
    hass,
) -> None:
    """Historical Store permissions are tightened without rewriting JSON."""
    store = Store[dict](hass, 1, "extended_openai_test.private_store")
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unchanged", encoding="utf-8")
    path.chmod(0o644)
    before = path.read_bytes()

    await _async_repair_private_store_mode(store)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == before


async def test_request_rules_store_writes_privately_and_atomically(
    hass, monkeypatch
) -> None:
    """Request Rules uses Home Assistant's supported private atomic Store path."""
    writes: list[bool] = []
    monkeypatch.setattr(
        "homeassistant.helpers.storage.write_utf8_file",
        lambda *_args, **_kwargs: pytest.fail("non-atomic Store writer used"),
    )
    monkeypatch.setattr(
        "homeassistant.helpers.storage.write_utf8_file_atomic",
        lambda _path, _data, private, **_kwargs: writes.append(private),
    )
    store = RequestRuleStore(
        hass, STORAGE_VERSION, "extended_openai_test.request_rules"
    )

    await store.async_save({"rules": []})

    assert writes == [True]


async def test_delayed_tool_store_writes_privately_and_atomically(
    hass, monkeypatch
) -> None:
    """Delayed tools use Home Assistant's supported private atomic Store path."""
    writes: list[bool] = []
    monkeypatch.setattr(
        "homeassistant.helpers.storage.write_utf8_file",
        lambda *_args, **_kwargs: pytest.fail("non-atomic Store writer used"),
    )
    monkeypatch.setattr(
        "homeassistant.helpers.storage.write_utf8_file_atomic",
        lambda _path, _data, private, **_kwargs: writes.append(private),
    )
    manager = DelayedToolManager(hass)

    await manager._store.async_save({"calls": []})

    assert writes == [True]


def test_repair_private_store_mode_changes_only_insecure_existing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chmod_calls: list[tuple[str, int]] = []
    current_mode = ph._PRIVATE_STORE_MODE
    fake_os = SimpleNamespace(
        stat=lambda _path: SimpleNamespace(st_mode=current_mode),
        chmod=lambda path, mode: chmod_calls.append((path, mode)),
    )
    monkeypatch.setattr(ph, "os", fake_os)

    ph._repair_private_store_mode("/config/.storage/already-private")
    assert chmod_calls == []

    fake_os.stat = lambda _path: SimpleNamespace(st_mode=0o644)
    ph._repair_private_store_mode("/config/.storage/insecure")
    assert chmod_calls == [("/config/.storage/insecure", ph._PRIVATE_STORE_MODE)]


def test_repair_private_store_mode_ignores_missing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(_path: str) -> Any:
        raise FileNotFoundError

    fake_os = SimpleNamespace(
        stat=_missing,
        chmod=lambda *_args: pytest.fail("missing files must not be chmodded"),
    )
    monkeypatch.setattr(ph, "os", fake_os)

    ph._repair_private_store_mode("/config/.storage/not-created-yet")
