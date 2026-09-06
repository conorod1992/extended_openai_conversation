"""Regressions for safe add_automation writes to automations.yaml."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from homeassistant.exceptions import HomeAssistantError

from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
    _append_automation_atomic,
    _restore_automation_file,
)


async def _disable_automation_validation(monkeypatch) -> None:
    monkeypatch.setattr(
        native.automation.config,
        "_async_validate_config_item",
        AsyncMock(),
    )


async def test_concurrent_add_automation_calls_preserve_both_entries(
    hass, monkeypatch
) -> None:
    await _disable_automation_validation(monkeypatch)
    function = NativeFunction()

    await asyncio.gather(
        function.add_automation(
            hass,
            {"name": "add_automation"},
            {"automation_config": "alias: First\ntriggers: []\nactions: []\n"},
            None,
            [],
        ),
        function.add_automation(
            hass,
            {"name": "add_automation"},
            {"automation_config": "alias: Second\ntriggers: []\nactions: []\n"},
            None,
            [],
        ),
    )

    document = yaml.safe_load(Path(hass.config.config_dir, "automations.yaml").read_text())
    assert [item["alias"] for item in document] == ["First", "Second"]
    assert len({item["id"] for item in document}) == 2


def test_append_aborts_when_external_writer_changes_file_before_replace(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "automations.yaml"
    original = "- id: existing\n  alias: Existing\n"
    external = "- id: external\n  alias: External edit\n"
    path.write_text(original, encoding="utf-8")
    real_fsync = native.os.fsync
    changed = False

    def fsync_and_external_edit(fd: int) -> None:
        nonlocal changed
        real_fsync(fd)
        if not changed:
            changed = True
            path.write_text(external, encoding="utf-8")

    monkeypatch.setattr(native.os, "fsync", fsync_and_external_edit)

    with pytest.raises(HomeAssistantError, match="changed.*please retry"):
        _append_automation_atomic(
            path,
            {"id": "ours", "alias": "Our automation", "triggers": [], "actions": []},
        )

    assert path.read_text(encoding="utf-8") == external


def test_rollback_refuses_to_overwrite_external_edit(tmp_path) -> None:
    path = tmp_path / "automations.yaml"
    previous = "- id: previous\n"
    written = "- id: previous\n- id: ours\n"
    external = "- id: external\n"
    path.write_text(external, encoding="utf-8")

    with pytest.raises(HomeAssistantError, match="changed.*please retry"):
        _restore_automation_file(path, previous, written)

    assert path.read_text(encoding="utf-8") == external


async def test_reload_failure_restores_original_file(hass, monkeypatch) -> None:
    await _disable_automation_validation(monkeypatch)
    path = Path(hass.config.config_dir, "automations.yaml")
    original = "- id: existing\n  alias: Existing\n"
    path.write_text(original, encoding="utf-8")
    hass.services.async_call = AsyncMock(side_effect=[RuntimeError("reload failed"), None])

    with pytest.raises(RuntimeError, match="reload failed"):
        await NativeFunction().add_automation(
            hass,
            {"name": "add_automation"},
            {"automation_config": "alias: New\ntriggers: []\nactions: []\n"},
            None,
            [],
        )

    assert path.read_text(encoding="utf-8") == original
    assert hass.services.async_call.await_count == 2


async def test_reload_failure_preserves_external_edit_during_rollback(
    hass, monkeypatch
) -> None:
    await _disable_automation_validation(monkeypatch)
    path = Path(hass.config.config_dir, "automations.yaml")
    path.write_text("- id: existing\n  alias: Existing\n", encoding="utf-8")
    external = "- id: external\n  alias: External edit\n"
    calls = 0

    async def reload_with_external_edit(*args, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            path.write_text(external, encoding="utf-8")
            raise RuntimeError("reload failed")

    hass.services.async_call = AsyncMock(side_effect=reload_with_external_edit)

    with pytest.raises(HomeAssistantError, match="external changes were preserved"):
        await NativeFunction().add_automation(
            hass,
            {"name": "add_automation"},
            {"automation_config": "alias: New\ntriggers: []\nactions: []\n"},
            None,
            [],
        )

    assert path.read_text(encoding="utf-8") == external
    assert hass.services.async_call.await_count == 1
