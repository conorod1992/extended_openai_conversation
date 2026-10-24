"""Regressions for safe add_automation writes to automations.yaml."""

from __future__ import annotations

import asyncio
import os
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


def _symlink(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target)
    except OSError as err:
        if os.name == "nt" and getattr(err, "winerror", None) == 1314:
            pytest.skip("Windows requires Developer Mode or symlink privileges")
        raise


@pytest.mark.parametrize("link_kind", ["absolute", "relative", "chain"])
@pytest.mark.parametrize("target_exists", [False, True])
@pytest.mark.parametrize("reload_fails", [False, True])
async def test_symlinked_automation_target_survives_write_and_rollback(
    hass, tmp_path, monkeypatch, link_kind, target_exists, reload_fails
):
    await _disable_automation_validation(monkeypatch)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    hass.config.config_dir = str(config_dir)
    target = tmp_path / "shared" / "rules.yaml"
    target.parent.mkdir()
    original = "# Shared automations\n- id: existing\n  alias: Existing\n"
    if target_exists:
        target.write_text(original, encoding="utf-8")
    path = config_dir / "automations.yaml"
    if link_kind == "chain":
        intermediate = config_dir / "shared-automations.yaml"
        _symlink(intermediate, target)
        _symlink(path, Path(intermediate.name))
    else:
        _symlink(
            path,
            target
            if link_kind == "absolute"
            else Path(os.path.relpath(target, config_dir)),
        )
    original_link = path.readlink()
    reloads = 0
    destinations = []
    replace = native.os.replace

    def record_replace(source, destination):
        destinations.append(Path(destination))
        return replace(source, destination)

    monkeypatch.setattr(native.os, "replace", record_replace)

    async def reload(*args, **kwargs):
        nonlocal reloads
        reloads += 1
        if reload_fails and reloads == 1:
            raise HomeAssistantError("reload failed")
        saved = (
            yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
        )
        hass.data[native.automation.DOMAIN] = SimpleNamespace(
            entities=[SimpleNamespace(unique_id=item["id"]) for item in saved]
        )

    hass.services.async_call = AsyncMock(side_effect=reload)
    call = NativeFunction().add_automation(
        hass,
        {},
        {"automation_config": "alias: New\ntriggers: []\nactions: []\n"},
        None,
        [],
    )
    if reload_fails:
        with pytest.raises(HomeAssistantError, match="reload failed"):
            await call
    else:
        assert await call == "Success"
    assert path.is_symlink()
    assert path.readlink() == original_link
    if link_kind == "chain":
        assert intermediate.is_symlink()
    assert destinations and all(destination == target for destination in destinations)
    if reload_fails:
        assert target.exists() is target_exists
        if target_exists:
            assert target.read_text(encoding="utf-8") == original
        hass.bus.async_fire.assert_not_called()
    else:
        saved = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert [item["alias"] for item in saved] == (
            ["Existing", "New"] if target_exists else ["New"]
        )
        assert path.read_bytes() == target.read_bytes()
        hass.bus.async_fire.assert_called_once()
    assert not list(target.parent.glob(".*.tmp-*"))


async def test_symlink_rollback_keeps_the_original_canonical_destination(
    hass, tmp_path, monkeypatch
):
    await _disable_automation_validation(monkeypatch)
    path = Path(hass.config.config_dir) / "automations.yaml"
    first, second = tmp_path / "first.yaml", tmp_path / "second.yaml"
    original = "- id: first\n  alias: First\n"
    other = "- id: second\n  alias: Second\n"
    first.write_text(original, encoding="utf-8")
    second.write_text(other, encoding="utf-8")
    _symlink(path, first)
    reloads = 0

    async def reload(*args, **kwargs):
        nonlocal reloads
        reloads += 1
        if reloads == 1:
            path.unlink()
            _symlink(path, second)
            raise HomeAssistantError("initial reload failed")

    hass.services.async_call = AsyncMock(side_effect=reload)
    with pytest.raises(HomeAssistantError, match="initial reload failed"):
        await NativeFunction().add_automation(
            hass,
            {},
            {"automation_config": "alias: New\ntriggers: []\nactions: []\n"},
            None,
            [],
        )
    assert path.is_symlink() and path.resolve() == second
    assert first.read_text(encoding="utf-8") == original
    assert second.read_text(encoding="utf-8") == other
    assert reloads == 2
    hass.bus.async_fire.assert_not_called()


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


async def test_automation_update_finishes_before_propagating_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = False

    async def operation() -> str:
        nonlocal completed
        started.set()
        await release.wait()
        completed = True
        return "automation_id"

    task = asyncio.create_task(native._async_settle_automation_update(operation()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed
