"""Regressions for safe add_automation writes to automations.yaml."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
    _append_automation_atomic,
    _restore_automation_file,
)
from homeassistant.exceptions import HomeAssistantError
from tests.lock_probe import LockProbe


async def _disable_automation_validation(monkeypatch) -> None:
    monkeypatch.setattr(
        native.automation.config,
        "_async_validate_config_item",
        AsyncMock(),
    )


@pytest.mark.parametrize("component_present", [False, True])
@pytest.mark.parametrize("file_exists", [False, True])
async def test_unloaded_automation_rolls_back_without_success(
    hass, monkeypatch, component_present, file_exists
):
    await _disable_automation_validation(monkeypatch)
    path = Path(hass.config.config_dir, "automations.yaml")
    original = "- id: existing\n  alias: Existing\n"
    if file_exists:
        path.write_text(original)
    if component_present:
        hass.data[native.automation.DOMAIN] = SimpleNamespace(entities=[])
    hass.services.async_call = AsyncMock()
    with pytest.raises(HomeAssistantError, match="not loaded.*automations.yaml"):
        await NativeFunction().add_automation(
            hass,
            {},
            {"automation_config": "alias: New\ntriggers: []\nactions: []\n"},
            None,
            [],
        )
    assert path.exists() is file_exists
    if file_exists:
        assert path.read_text() == original
    assert hass.services.async_call.await_count == 2
    hass.bus.async_fire.assert_not_called()


async def test_concurrent_add_automation_calls_preserve_both_entries(
    hass, monkeypatch
) -> None:
    await _disable_automation_validation(monkeypatch)
    function = NativeFunction()

    probe = LockProbe(asyncio.Lock())
    hass.data[native._AUTOMATION_WRITE_LOCK_KEY] = probe
    reload_entered = asyncio.Event()
    release_reload = asyncio.Event()
    reloads = []
    original_append = native._append_automation_atomic
    writes = []

    def append(*args):
        writes.append(args)
        return original_append(*args)

    async def reload(*args, **kwargs):
        reloads.append(args)
        if len(reloads) == 1:
            reload_entered.set()
            await release_reload.wait()
        document = yaml.safe_load(
            Path(hass.config.config_dir, "automations.yaml").read_text()
        )
        hass.data[native.automation.DOMAIN] = SimpleNamespace(
            entities=[SimpleNamespace(unique_id=item["id"]) for item in document]
        )

    monkeypatch.setattr(native, "_append_automation_atomic", append)
    hass.services.async_call = AsyncMock(side_effect=reload)
    first = asyncio.create_task(
        function.add_automation(
            hass,
            {"name": "add_automation"},
            {"automation_config": "alias: First\ntriggers: []\nactions: []\n"},
            None,
            [],
        )
    )
    await asyncio.wait_for(reload_entered.wait(), timeout=5)
    assert await probe.next_attempt() is first
    second = asyncio.create_task(
        function.add_automation(
            hass,
            {"name": "add_automation"},
            {"automation_config": "alias: Second\ntriggers: []\nactions: []\n"},
            None,
            [],
        )
    )
    try:
        assert await probe.next_attempt() is second
        assert not second.done()
        assert len(writes) == len(reloads) == 1
    finally:
        release_reload.set()
        await asyncio.gather(first, second)
    assert len(writes) == len(reloads) == 2

    document = yaml.safe_load(
        Path(hass.config.config_dir, "automations.yaml").read_text()
    )
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
    hass.services.async_call = AsyncMock(
        side_effect=[RuntimeError("reload failed"), None]
    )

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


@pytest.fixture(autouse=True)
def authenticated_automation_admin(hass):
    from types import SimpleNamespace

    from custom_components.extended_openai_conversation_responses.ha_permissions import (
        bind_active_ha_context,
    )
    from homeassistant.core import Context

    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_active=True, is_admin=True)
    )
    with bind_active_ha_context(Context(user_id="admin")):
        yield
