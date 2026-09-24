"""Real-HA process-crash coverage for immediate Function Tool side effects."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tests_real_ha.process_harness import child_process_env

DOMAIN = "extended_openai_conversation_responses"
_CHILD_PHASE = "IMMEDIATE_TOOL_CRASH_PHASE"
_CONFIG_DIR_ENV = "IMMEDIATE_TOOL_CRASH_CONFIG_DIR"
_SIDE_EFFECTS_FILE = "immediate-tool-side-effects.jsonl"
_EFFECT_RECORDED_FILE = "immediate-tool-effect-recorded"
_SERVICE_DOMAIN = "immediate_crash_probe"
_SERVICE_NAME = "record"
_TOOL_NAME = "immediate_crash_probe"
_FIRST_MARKER = "before-process-crash"
_RECOVERY_MARKER = "after-process-restart"


def _tool_config() -> dict[str, Any]:
    return {
        "spec": {
            "name": _TOOL_NAME,
            "description": "Record one deterministic immediate process-crash probe.",
            "parameters": {
                "type": "object",
                "properties": {"marker": {"type": "string"}},
                "required": ["marker"],
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": f"{_SERVICE_DOMAIN}.{_SERVICE_NAME}",
                    "data": {"marker": "{{ marker }}"},
                }
            ],
        },
        "enabled": True,
    }


def _stage_component(source: Path, destination: Path) -> None:
    """Stage the integration while omitting heavyweight unrelated HA dependencies."""
    shutil.copytree(source, destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    excluded = {"energy", "history", "recorder"}
    manifest["dependencies"] = [
        dependency
        for dependency in manifest.get("dependencies", [])
        if dependency not in excluded
    ]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _assert_source_component(config_dir: Path) -> None:
    module = importlib.import_module(f"custom_components.{DOMAIN}")
    module_path = Path(module.__file__).resolve()
    component_root = (config_dir / "custom_components" / DOMAIN).resolve()
    assert component_root in module_path.parents


def _append_effect(config_dir: Path, marker: str) -> None:
    path = config_dir / _SIDE_EFFECTS_FILE
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"marker": marker}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_effects(config_dir: Path) -> list[dict[str, Any]]:
    path = config_dir / _SIDE_EFFECTS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _ensure_entry(hass: Any) -> Any:
    """Create the integration once, then reuse the persisted entry after restart."""
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.data_entry_flow import FlowResultType

    entries = hass.config_entries.async_entries(DOMAIN)
    if entries:
        assert len(entries) == 1
        entry = entries[0]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        return entry

    config_flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    authenticate = AsyncMock(return_value=object())
    with patch.object(config_flow, "get_authenticated_client", authenticate):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Immediate Tool Crash Boundary",
                CONF_API_KEY: "sk-immediate-tool-crash",
                const.CONF_BASE_URL: const.DEFAULT_CONF_BASE_URL,
                const.CONF_SKIP_AUTHENTICATION: True,
                const.CONF_API_PROVIDER: "openai",
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _configure_tool(hass: Any, entry: Any) -> Any:
    from homeassistant.components import conversation

    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    updated = dict(subentry.data)
    updated[const.CONF_FUNCTION_TOOLS] = yaml.safe_dump(
        [_tool_config()], sort_keys=False, allow_unicode=True
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=updated)
    await hass.async_block_till_done()
    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    return agent


async def _configured_tool(agent: Any) -> dict[str, Any]:
    agent_config = importlib.import_module(f"custom_components.{DOMAIN}.agent_config")
    configured = agent_config.configured_function_tools_from_data(agent.subentry.data)
    return next(tool for tool in configured if tool["spec"]["name"] == _TOOL_NAME)


async def _execute_tool(hass: Any, agent: Any, marker: str) -> Any:
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    tool = await _configured_tool(agent)
    user = await hass.auth.async_create_user(f"Immediate Crash {marker}")
    return await agent._execute_function_tool(
        tool,
        llm.ToolInput(
            id=f"call-{marker}",
            tool_name=_TOOL_NAME,
            tool_args={"marker": marker},
            external=True,
        ),
        SimpleNamespace(context=Context(user_id=user.id), device_id=None),
        [],
    )


async def _crash_phase(config_dir: Path) -> None:
    """Execute the side effect, then remain blocked until the parent SIGKILLs HA."""
    from homeassistant import bootstrap, runner

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None

    async def record(call: Any) -> None:
        _append_effect(config_dir, str(call.data["marker"]))
        (config_dir / _EFFECT_RECORDED_FILE).write_text("recorded\n", encoding="utf-8")
        # The externally visible side effect is durable now, but the service call and
        # Function Tool have not returned a result. Hold this exact ambiguity window
        # until the parent hard-kills the Home Assistant process.
        await asyncio.Event().wait()

    hass.services.async_register(_SERVICE_DOMAIN, _SERVICE_NAME, record)
    await hass.async_start()
    entry = await _ensure_entry(hass)
    agent = await _configure_tool(hass, entry)

    # Wait for the exact entry and Function Tool configuration needed on reboot.
    # HA writes config entries on a delayed schedule; an in-memory loaded entry
    # alone does not prove the recovery process will see the tool.
    await hass.async_block_till_done()
    const = importlib.import_module(f"custom_components.{DOMAIN}.const")
    storage_path = config_dir / ".storage" / "core.config_entries"
    async with asyncio.timeout(15):
        while True:
            try:
                payload = json.loads(storage_path.read_text(encoding="utf-8"))
                entries = payload["data"]["entries"]
                persisted = next(
                    item for item in entries if item["entry_id"] == entry.entry_id
                )
                subentries = persisted["subentries"]
                if any(
                    item["subentry_type"] == "conversation"
                    and _TOOL_NAME in item["data"].get(const.CONF_FUNCTION_TOOLS, "")
                    for item in subentries
                ):
                    break
            except (FileNotFoundError, json.JSONDecodeError, KeyError, StopIteration):
                pass
            await asyncio.sleep(0.05)

    await _execute_tool(hass, agent, _FIRST_MARKER)
    raise AssertionError("immediate tool unexpectedly returned past crash boundary")


async def _recovery_phase(config_dir: Path) -> None:
    """Cold-start HA, prove no automatic replay, then execute a fresh call."""
    from homeassistant import bootstrap, runner
    from homeassistant.components import conversation
    from homeassistant.config_entries import ConfigEntryState

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None

    async def record(call: Any) -> None:
        _append_effect(config_dir, str(call.data["marker"]))

    hass.services.async_register(_SERVICE_DOMAIN, _SERVICE_NAME, record)
    await hass.async_start()
    try:
        _assert_source_component(config_dir)
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        entry = entries[0]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        # Startup must not infer that the interrupted immediate operation should be
        # resumed or replayed. Give startup tasks enough time to expose such a bug.
        await asyncio.sleep(0.5)
        await hass.async_block_till_done()
        assert _read_effects(config_dir) == [{"marker": _FIRST_MARKER}]

        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None
        await _execute_tool(hass, agent, _RECOVERY_MARKER)
        await hass.async_block_till_done()
        assert _read_effects(config_dir) == [
            {"marker": _FIRST_MARKER},
            {"marker": _RECOVERY_MARKER},
        ]
    finally:
        await hass.async_stop()


async def _child_main() -> None:
    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    sys.path.insert(0, str(config_dir))
    _assert_source_component(config_dir)
    phase = os.environ[_CHILD_PHASE]
    if phase == "crash":
        await _crash_phase(config_dir)
    elif phase == "recover":
        await _recovery_phase(config_dir)
    else:
        raise AssertionError(f"Unknown immediate-tool crash phase: {phase}")


def _child_env(config_dir: Path, phase: str) -> dict[str, str]:
    return child_process_env(
        __file__,
        {
            _CHILD_PHASE: phase,
            _CONFIG_DIR_ENV: str(config_dir),
        },
    )


def test_immediate_tool_side_effect_is_not_replayed_after_process_crash(
    tmp_path: Path,
) -> None:
    """A crash after an immediate side effect must not cause automatic replay."""
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    _stage_component(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Immediate Tool Crash Boundary\n",
        encoding="utf-8",
    )

    crash = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=_child_env(config_dir, "crash"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(300):
            if (config_dir / _EFFECT_RECORDED_FILE).exists():
                break
            if crash.poll() is not None:
                output, _ = crash.communicate()
                raise AssertionError(
                    "immediate-tool crash child exited before the side effect boundary:\n"
                    + output
                )
            time.sleep(0.1)
        else:
            crash.kill()
            output, _ = crash.communicate(timeout=10)
            raise AssertionError(
                "timed out waiting for immediate-tool side effect boundary:\n" + output
            )

        assert _read_effects(config_dir) == [{"marker": _FIRST_MARKER}]
        crash.kill()
        crash.wait(timeout=10)
    finally:
        if crash.poll() is None:
            crash.kill()
            crash.wait(timeout=10)

    recovered = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=config_dir,
        env=_child_env(config_dir, "recover"),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert recovered.returncode == 0, (
        "immediate-tool recovery process failed\n"
        f"stdout:\n{recovered.stdout}\n\nstderr:\n{recovered.stderr}"
    )
    assert _read_effects(config_dir) == [
        {"marker": _FIRST_MARKER},
        {"marker": _RECOVERY_MARKER},
    ]


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE):
    asyncio.run(_child_main())
