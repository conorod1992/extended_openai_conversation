"""Focused coverage for persistence transaction recovery boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.extended_openai_conversation_responses import (
    persistence_hardening as ph,
)


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
