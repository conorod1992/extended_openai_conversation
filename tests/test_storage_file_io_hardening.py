"""Regression tests for durable Store privacy and bounded atomic file writes."""

from __future__ import annotations

from pathlib import Path
import stat

import pytest

from homeassistant.helpers.storage import Store

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
    _async_prepare_private_store,
    install_persistence_transactions,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
    RequestRuleStore,
    STORAGE_VERSION,
)


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


async def test_private_store_hardening_repairs_existing_file_without_rewrite(
    hass,
) -> None:
    """An existing public Store is tightened before future private atomic writes."""
    store = Store[dict](hass, 1, "extended_openai_test.private_store")
    path = Path(store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unchanged", encoding="utf-8")
    path.chmod(0o644)
    before = path.read_bytes()

    await _async_prepare_private_store(store)

    assert store._private is True
    assert store._atomic_writes is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_bytes() == before


async def test_request_rules_store_is_hardened_before_initialize(hass) -> None:
    """The existing Request Rules manager guard prepares its real HA Store."""
    install_persistence_transactions()
    store = RequestRuleStore(hass, STORAGE_VERSION, "extended_openai_test.request_rules")
    rules = RequestRules(store)

    await rules.async_initialize()

    assert store._private is True
    assert store._atomic_writes is True


async def test_delayed_tool_store_is_hardened_before_setup(hass) -> None:
    """Delayed-tool recovery uses the same private atomic Store policy."""
    install_persistence_transactions()
    manager = DelayedToolManager(hass)

    await manager.async_setup()

    assert manager._store._private is True
    assert manager._store._atomic_writes is True
