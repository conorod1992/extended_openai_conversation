"""Cold-restart checks at the exact completed Store write boundary."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests_real_ha.process_harness import child_process_env
from tests_real_ha.test_immediate_tool_process_crash import (
    DOMAIN,
    _assert_source_component,
    _ensure_entry,
    _stage_component,
)

_PHASE = "STORE_CRASH_PHASE"
_KIND = "STORE_CRASH_KIND"
_CONFIG = "STORE_CRASH_CONFIG_DIR"
_OWNER = "store-crash-owner"


async def _manager(hass, entry, kind):
    from custom_components.extended_openai_conversation_responses.knowledge import (
        async_get_knowledge,
    )
    from custom_components.extended_openai_conversation_responses.memory import (
        async_get_memory,
    )

    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    getter = async_get_memory if kind == "memory" else async_get_knowledge
    return await getter(hass, entry.entry_id, subentry.subentry_id)


async def _child(config_dir: Path, phase: str, kind: str) -> None:
    from homeassistant import bootstrap, runner
    from homeassistant.components import conversation
    from homeassistant.config_entries import ConfigEntryState

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()
    entry = await _ensure_entry(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert conversation.async_get_agent(hass, entry.entry_id) is not None
    manager = await _manager(hass, entry, kind)

    if phase == "recover":
        try:
            if kind == "memory":
                records = await manager.async_list(_OWNER)
                assert len(records) == 1
                assert records[0].content == "Committed process boundary memory"
            else:
                sources = await manager.async_list()
                assert len(sources) == 1
                source = await manager.async_get(sources[0]["source_id"])
                assert source.content == "Committed process boundary knowledge"
        finally:
            await hass.async_stop()
        return

    assert phase == "crash"
    # Config entries use a delayed write. Wait until the recovery child can see
    # the entry before beginning the Store operation under test.
    entry_store = config_dir / ".storage" / "core.config_entries"
    async with asyncio.timeout(15):
        while True:
            try:
                payload = json.loads(entry_store.read_text(encoding="utf-8"))
                if any(
                    item["entry_id"] == entry.entry_id
                    and any(
                        sub["subentry_type"] == "conversation"
                        for sub in item["subentries"]
                    )
                    for item in payload["data"]["entries"]
                ):
                    break
            except FileNotFoundError, json.JSONDecodeError, KeyError:
                pass
            await asyncio.sleep(0.05)

    storage = manager._storage
    original_save = storage.async_save

    async def pause_after_commit(data):
        await original_save(data)
        # Store.async_save has completed its atomic write. The parent kills the
        # child here, before the public mutation returns a success response.
        (config_dir / f"{kind}-committed").write_text("committed\n", encoding="utf-8")
        await asyncio.Event().wait()

    storage.async_save = pause_after_commit
    if kind == "memory":
        await manager.async_add(
            _OWNER, "Committed process boundary memory", "general", "explicit"
        )
    else:
        await manager.async_create(
            "Process boundary knowledge", "", "Committed process boundary knowledge"
        )
    raise AssertionError("mutation returned before the process was killed")


def _env(config_dir: Path, phase: str, kind: str) -> dict[str, str]:
    return child_process_env(
        __file__, {_PHASE: phase, _KIND: kind, _CONFIG: str(config_dir)}
    )


@pytest.mark.parametrize("kind", ["memory", "knowledge"])
def test_completed_store_write_survives_process_kill(tmp_path: Path, kind: str) -> None:
    source = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    _stage_component(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Store Crash Boundary\n", encoding="utf-8"
    )

    crash = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=_env(config_dir, "crash", kind),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(300):
            if (config_dir / f"{kind}-committed").exists():
                break
            if crash.poll() is not None:
                output, _ = crash.communicate()
                raise AssertionError(
                    f"{kind} child exited before commit boundary:\n{output}"
                )
            time.sleep(0.1)
        else:
            crash.kill()
            output, _ = crash.communicate(timeout=10)
            raise AssertionError(f"{kind} commit boundary not reached:\n{output}")
        crash.kill()
        crash.wait(timeout=10)
    finally:
        if crash.poll() is None:
            crash.kill()
            crash.wait(timeout=10)

    recovered = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=_env(config_dir, "recover", kind),
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    assert recovered.returncode == 0, (
        f"{kind} cold restart failed\nstdout:\n{recovered.stdout}\nstderr:\n{recovered.stderr}"
    )
    artifact_dir = Path(os.environ.get("STRESS_ARTIFACT_DIR", "stress-artifacts"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / f"process-store-{kind}.json").write_text(
        json.dumps(
            {
                "test": f"process-store-{kind}",
                "operations": [
                    {
                        "operation": "summary",
                        "layer": "process-boundary",
                        "process_terminations": 1,
                        "store_boundary": kind,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__" and os.environ.get(_PHASE):
    config_dir = Path(os.environ[_CONFIG]).resolve()
    sys.path.insert(0, str(config_dir))
    _assert_source_component(config_dir)
    asyncio.run(_child(config_dir, os.environ[_PHASE], os.environ[_KIND]))
