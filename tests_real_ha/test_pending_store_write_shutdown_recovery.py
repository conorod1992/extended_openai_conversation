"""Real-HA persistence integrity when shutdown interrupts a Store-backed mutation."""

from __future__ import annotations

import asyncio
from datetime import timedelta
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
_CHILD_PHASE = "PENDING_STORE_WRITE_PHASE"
_COMMIT_MODE = "PENDING_STORE_WRITE_COMMIT_MODE"
_CONFIG_DIR = "PENDING_STORE_WRITE_CONFIG_DIR"
_ENTRY_ID = "pending-store-write-entry"
_SUBENTRY_ID = "pending-store-write-agent"
_SCOPE_ID = "user:pending-store-write-user"
_OLD_MARKER = "old-complete-temporary-memory"
_NEW_MARKER = "new-complete-temporary-memory"
_RECOVERY_MARKER = "post-restart-temporary-memory"


def _contents(records: list[Any]) -> list[str]:
    """Return deterministic Temporary Memory contents."""
    return sorted(str(record.content) for record in records)


def _expected_contents(mode: str) -> list[str]:
    """Return the only legal durable state after the interrupted mutation."""
    if mode == "before_commit":
        return [_OLD_MARKER]
    if mode == "after_commit":
        return sorted([_OLD_MARKER, _NEW_MARKER])
    raise AssertionError(f"Unknown commit mode: {mode}")


def _storage_path(config_dir: Path) -> Path:
    """Return the real Home Assistant Store path for this acceptance test."""
    storage_key_prefix = f"{DOMAIN}.temporary_memory"
    key = f"{storage_key_prefix}.{_ENTRY_ID}.{_SUBENTRY_ID}"
    return config_dir / ".storage" / key


def _raw_record_contents(config_dir: Path) -> list[str]:
    """Parse the real Store envelope and return persisted record contents."""
    payload = json.loads(_storage_path(config_dir).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload.get("version") == 1
    data = payload.get("data")
    assert isinstance(data, dict)
    records = data.get("records")
    assert isinstance(records, list)
    assert all(isinstance(record, dict) for record in records)
    return sorted(str(record["content"]) for record in records)


async def _new_manager(hass: Any):
    """Construct a fresh manager over the production HA Store adapter."""
    from custom_components.extended_openai_conversation_responses.temporary_memory import (
        TemporaryMemory,
        _temporary_memory_store,
    )

    manager = TemporaryMemory(_temporary_memory_store(hass, _ENTRY_ID, _SUBENTRY_ID))
    await manager.async_initialize()
    return manager


async def _interrupt_phase(hass: Any, config_dir: Path, mode: str) -> None:
    """Stop Home Assistant while a mutation is held at the Store save boundary."""
    from homeassistant.util import dt as dt_util

    manager = await _new_manager(hass)
    expires_at = (dt_util.utcnow() + timedelta(days=1)).isoformat()
    await manager.async_add(
        _SCOPE_ID,
        _OLD_MARKER,
        expires_at,
        "acceptance",
        owner_scope_id=_SCOPE_ID,
    )
    assert _contents(await manager.async_active(_SCOPE_ID, _SCOPE_ID)) == [_OLD_MARKER]
    assert _raw_record_contents(config_dir) == [_OLD_MARKER]

    store = manager._store
    real_save = store.async_save
    save_reached = asyncio.Event()
    save_cancelled = asyncio.Event()

    async def interrupted_write(data: dict[str, Any]) -> None:
        # Exercise both valid atomic outcomes around the real Store commit point.
        if mode == "after_commit":
            await real_save(data)
        save_reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            save_cancelled.set()
            raise

    async def gated_save(data: dict[str, Any]) -> None:
        # The transactional owner shields Store writes from caller cancellation.
        # Give this deliberately non-terminating I/O double a real HA shutdown
        # owner so the write itself reaches a known cancelled outcome at stop.
        # An untracked infinite gate would never settle, unlike a real Store write.
        await hass.async_create_background_task(
            interrupted_write(data), "acceptance_pending_store_write"
        )

    with patch.object(store, "async_save", gated_save):
        mutation_task = hass.async_create_task(
            manager.async_add(
                _SCOPE_ID,
                _NEW_MARKER,
                expires_at,
                "acceptance",
                owner_scope_id=_SCOPE_ID,
            )
        )
        async with asyncio.timeout(15):
            await save_reached.wait()

        # async_add() still owns the manager lock while it awaits Store.async_save(),
        # so inspect the already-mutated private record set here rather than trying
        # to re-enter the lock through a public snapshot method.
        assert _NEW_MARKER in {record.content for record in manager._records.values()}
        # A cancelled caller must not tear down an in-flight Store write. The
        # manager is now transactional even before integration installers run.
        mutation_task.cancel()
        await asyncio.sleep(0)
        assert not mutation_task.done()
        assert not save_cancelled.is_set()

        async with asyncio.timeout(20):
            await hass.async_stop()
        # Allow the shielded save and its mutation to observe HA's cancellation.
        async with asyncio.timeout(5):
            await asyncio.gather(mutation_task, return_exceptions=True)

        assert save_cancelled.is_set(), (
            "pending Store save did not receive cancellation"
        )
        assert mutation_task.done(), "Store-backed mutation survived HA shutdown"
        assert mutation_task.cancelled(), "interrupted Store mutation was not cancelled"

    # Do not rely on the in-memory manager after shutdown. Only confirm the on-disk
    # file remains parseable and is exactly one complete allowed generation.
    assert _raw_record_contents(config_dir) == _expected_contents(mode)


async def _recover_phase(hass: Any, config_dir: Path, mode: str) -> None:
    """Cold-start the Store, verify integrity, mutate it again, and reload once more."""
    from homeassistant.util import dt as dt_util

    expected = _expected_contents(mode)
    manager = await _new_manager(hass)
    loaded = await manager.async_active(_SCOPE_ID, _SCOPE_ID)
    assert _contents(loaded) == expected
    assert _raw_record_contents(config_dir) == expected

    # The recovered manager must remain writable. A damaged/truncated file can
    # occasionally parse far enough to load but fail on the next production save.
    expires_at = (dt_util.utcnow() + timedelta(days=1)).isoformat()
    await manager.async_add(
        _SCOPE_ID,
        _RECOVERY_MARKER,
        expires_at,
        "acceptance",
        owner_scope_id=_SCOPE_ID,
    )
    expected_after_write = sorted([*expected, _RECOVERY_MARKER])
    assert (
        _contents(await manager.async_active(_SCOPE_ID, _SCOPE_ID))
        == expected_after_write
    )
    assert _raw_record_contents(config_dir) == expected_after_write

    # Re-open through a brand-new Store/manager instance to prove the rewritten
    # canonical payload is independently loadable rather than merely cached in RAM.
    reloaded = await _new_manager(hass)
    assert (
        _contents(await reloaded.async_active(_SCOPE_ID, _SCOPE_ID))
        == expected_after_write
    )


async def _child_main() -> None:
    """Run one independent Home Assistant process for one acceptance phase."""
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
        # This phase performs the actual HA shutdown while the production Store save
        # is demonstrably pending, so do not stop HA again in a finally block.
        await _interrupt_phase(hass, config_dir, mode)
        return

    try:
        if phase == "recover":
            await _recover_phase(hass, config_dir, mode)
        else:
            raise AssertionError(f"Unknown pending-write phase: {phase}")
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def _run_child(
    config_dir: Path, phase: str, mode: str
) -> subprocess.CompletedProcess[str]:
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


def _assert_child_ok(
    result: subprocess.CompletedProcess[str], phase: str, mode: str
) -> None:
    assert result.returncode == 0, (
        f"pending Store write phase {phase!r}/{mode!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("mode", ["before_commit", "after_commit"])
def test_shutdown_during_pending_store_write_recovers_complete_generation(
    tmp_path: Path,
    mode: str,
) -> None:
    """Shutdown around Store commit yields old-complete or new-complete state only."""
    repo_root = Path(__file__).resolve().parent.parent
    source = repo_root / "custom_components" / DOMAIN
    config_dir = tmp_path / f"ha-config-{mode}"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Pending Store Write Acceptance\n",
        encoding="utf-8",
    )

    interrupted = _run_child(config_dir, "interrupt", mode)
    _assert_child_ok(interrupted, "interrupt", mode)
    assert _storage_path(config_dir).exists()
    assert _raw_record_contents(config_dir) == _expected_contents(mode)

    recovered = _run_child(config_dir, "recover", mode)
    _assert_child_ok(recovered, "recover", mode)


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE):
    asyncio.run(_child_main())
