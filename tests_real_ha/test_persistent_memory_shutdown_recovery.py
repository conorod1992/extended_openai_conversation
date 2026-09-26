"""Process-level graceful HA shutdown at a persistent Memory commit boundary."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from tests_real_ha.process_harness import run_python_child

DOMAIN = "extended_openai_conversation_responses"
_CHILD_PHASE = "PERSISTENT_MEMORY_SHUTDOWN_PHASE"
_COMMIT_MODE = "PERSISTENT_MEMORY_SHUTDOWN_COMMIT_MODE"
_CONFIG_DIR = "PERSISTENT_MEMORY_SHUTDOWN_CONFIG_DIR"
_ENTRY_ID = "persistent-memory-shutdown-entry"
_SUBENTRY_ID = "persistent-memory-shutdown-agent"
_OWNER = "persistent-memory-shutdown-user"
_OLD = "persistent-memory-old-generation"
_NEW = "persistent-memory-new-generation"
_RECOVERY = "persistent-memory-post-restart"


def _storage_path(config_dir: Path) -> Path:
    return config_dir / ".storage" / f"{DOMAIN}.memory.{_ENTRY_ID}.{_SUBENTRY_ID}"


def _contents(config_dir: Path) -> list[str]:
    payload = json.loads(_storage_path(config_dir).read_text(encoding="utf-8"))
    assert payload["version"] == 2
    return sorted(str(item["content"]) for item in payload["data"]["memories"])


def _expected(mode: str) -> list[str]:
    return [_OLD] if mode == "before_commit" else sorted([_OLD, _NEW])


async def _manager(hass: Any):
    from custom_components.extended_openai_conversation_responses.memory import (
        HomeAssistantMemoryStorage,
        PersistentMemory,
    )

    manager = PersistentMemory(
        HomeAssistantMemoryStorage(hass, _ENTRY_ID, _SUBENTRY_ID)
    )
    await manager.async_initialize()
    return manager


async def _interrupt(hass: Any, config_dir: Path, mode: str) -> None:
    manager = await _manager(hass)
    await manager.async_add(_OWNER, _OLD, "acceptance", "explicit")
    assert _contents(config_dir) == [_OLD]

    storage = manager._storage
    real_save = storage.async_save
    save_reached = asyncio.Event()
    save_cancelled = asyncio.Event()

    async def interrupted_write(data: dict[str, Any]) -> None:
        if mode == "after_commit":
            await real_save(data)
        save_reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            save_cancelled.set()
            raise

    async def gated_save(data: dict[str, Any]) -> None:
        await hass.async_create_background_task(
            interrupted_write(data), "acceptance_pending_persistent_memory_write"
        )

    with patch.object(storage, "async_save", gated_save):
        mutation = hass.async_create_task(
            manager.async_add(_OWNER, _NEW, "acceptance", "explicit")
        )
        async with asyncio.timeout(15):
            await save_reached.wait()
        assert _NEW in {item.content for item in manager._memories.values()}

        mutation.cancel()
        await asyncio.sleep(0)
        assert not mutation.done()
        assert not save_cancelled.is_set()

        async with asyncio.timeout(20):
            await hass.async_stop()
        async with asyncio.timeout(5):
            await asyncio.gather(mutation, return_exceptions=True)

        assert save_cancelled.is_set()
        assert mutation.done() and mutation.cancelled()

    assert _contents(config_dir) == _expected(mode)


async def _recover(hass: Any, config_dir: Path, mode: str) -> None:
    manager = await _manager(hass)
    assert sorted(item.content for item in await manager.async_list(_OWNER)) == _expected(mode)
    await manager.async_add(_OWNER, _RECOVERY, "acceptance", "explicit")
    expected = sorted([*_expected(mode), _RECOVERY])
    assert sorted(item.content for item in await manager.async_list(_OWNER)) == expected
    assert _contents(config_dir) == expected


async def _child_main() -> None:
    from homeassistant import bootstrap, runner

    config_dir = Path(os.environ[_CONFIG_DIR]).resolve()
    phase = os.environ[_CHILD_PHASE]
    mode = os.environ[_COMMIT_MODE]
    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()

    if phase == "interrupt":
        await _interrupt(hass, config_dir, mode)
        return
    try:
        await _recover(hass, config_dir, mode)
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def _run(config_dir: Path, phase: str, mode: str) -> subprocess.CompletedProcess[str]:
    return run_python_child(
        __file__,
        cwd=config_dir,
        extra_env={
            _CHILD_PHASE: phase,
            _COMMIT_MODE: mode,
            _CONFIG_DIR: str(config_dir),
        },
        timeout=90,
    )


@pytest.mark.parametrize("mode", ["before_commit", "after_commit"])
def test_graceful_shutdown_during_persistent_memory_write_recovers_cleanly(
    tmp_path: Path, mode: str
) -> None:
    """Shutdown leaves either the old or fully committed generation, never a hybrid."""
    source = Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
    config_dir = tmp_path / f"ha-config-{mode}"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Persistent Memory Shutdown Acceptance\n",
        encoding="utf-8",
    )

    interrupted = _run(config_dir, "interrupt", mode)
    assert interrupted.returncode == 0, (
        f"interrupt/{mode} failed\nstdout:\n{interrupted.stdout}\nstderr:\n{interrupted.stderr}"
    )
    assert _contents(config_dir) == _expected(mode)

    recovered = _run(config_dir, "recover", mode)
    assert recovered.returncode == 0, (
        f"recover/{mode} failed\nstdout:\n{recovered.stdout}\nstderr:\n{recovered.stderr}"
    )


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE):
    asyncio.run(_child_main())
