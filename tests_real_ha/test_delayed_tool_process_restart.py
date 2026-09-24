"""Real-HA process restart coverage for durable delayed Function Tools."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from tests_real_ha.process_harness import run_python_child

DOMAIN = "extended_openai_conversation_responses"
_CHILD_PHASE = "DELAYED_TOOL_PROCESS_PHASE"
_CONFIG_DIR_ENV = "DELAYED_TOOL_PROCESS_CONFIG_DIR"
_METADATA_FILE = "delayed-tool-process-metadata.json"
_EXECUTIONS_FILE = "delayed-tool-process-executions.jsonl"
_STORE_KEY = f"{DOMAIN}.delayed_tools"
_SERVICE_DOMAIN = "delayed_restart_probe"
_SERVICE_NAME = "record"
_TOOL_NAME = "delayed_restart_probe"
_MARKER = "persisted-delayed-tool"
_DEVICE_ID = "delayed-restart-device"
_PAST_DUE = "2000-01-01T00:00:00+00:00"


def _tool_config() -> dict[str, Any]:
    """Return a configured script tool using the documented legacy delay field."""
    return {
        "spec": {
            "name": _TOOL_NAME,
            "description": "Record one deterministic delayed restart probe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "marker": {"type": "string"},
                    "delay": {
                        "type": "object",
                        "properties": {
                            "seconds": {"type": "number", "minimum": 0}
                        },
                        "required": ["seconds"],
                        "additionalProperties": False,
                    },
                },
                "required": ["marker", "delay"],
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


def _assert_source_component(config_dir: Path) -> None:
    """Ensure each child is exercising the copied integration under its HA config."""
    module = importlib.import_module(f"custom_components.{DOMAIN}")
    module_path = Path(module.__file__).resolve()
    component_root = (config_dir / "custom_components" / DOMAIN).resolve()
    assert component_root in module_path.parents, (
        "child Home Assistant process imported the integration outside its staged "
        f"config directory: {module_path}"
    )


def _stage_component(source: Path, destination: Path) -> None:
    """Stage the integration while excluding unrelated heavyweight HA dependencies."""
    shutil.copytree(source, destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # This subprocess regression is specifically about delayed-tool persistence across
    # a real HA process boundary. energy/history/recorder are unrelated to that path
    # and require optional runtime/system pieces absent from the lightweight Real-HA
    # CI child environment. Keep every other dependency and the production code exact.
    excluded = {"energy", "history", "recorder"}
    manifest["dependencies"] = [
        dependency
        for dependency in manifest.get("dependencies", [])
        if dependency not in excluded
    ]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


async def _create_entry_and_schedule(config_dir: Path) -> None:
    """First process: create the entry, persist a pending call, then stop HA."""
    from homeassistant import bootstrap, runner
    from homeassistant.components import conversation
    from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
    from homeassistant.const import CONF_API_KEY, CONF_NAME
    from homeassistant.core import Context
    from homeassistant.data_entry_flow import FlowResultType
    from homeassistant.helpers import llm

    sys.path.insert(0, str(config_dir))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await hass.async_start()

    try:
        agent_config = importlib.import_module(
            f"custom_components.{DOMAIN}.agent_config"
        )
        config_flow = importlib.import_module(f"custom_components.{DOMAIN}.config_flow")
        const = importlib.import_module(f"custom_components.{DOMAIN}.const")
        delayed_tools = importlib.import_module(
            f"custom_components.{DOMAIN}.delayed_tools"
        )
        _assert_source_component(config_dir)

        authenticate = AsyncMock(return_value=object())
        with patch.object(config_flow, "get_authenticated_client", authenticate):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            assert result["type"] is FlowResultType.FORM
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_NAME: "Delayed Tool Process Restart",
                    CONF_API_KEY: "sk-delayed-process-restart",
                    const.CONF_BASE_URL: const.DEFAULT_CONF_BASE_URL,
                    const.CONF_SKIP_AUTHENTICATION: True,
                    const.CONF_API_PROVIDER: "openai",
                },
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        subentry = next(
            item
            for item in entry.subentries.values()
            if item.subentry_type == "conversation"
        )
        updated = dict(subentry.data)
        # Production agent storage keeps Function Tools as YAML text. Persist the
        # acceptance fixture through that same representation so a cold process
        # exercises the real deserialize/validate path rather than an impossible
        # in-memory list shape.
        updated[const.CONF_FUNCTION_TOOLS] = yaml.safe_dump(
            [_tool_config()], sort_keys=False, allow_unicode=True
        )
        hass.config_entries.async_update_subentry(entry, subentry, data=updated)
        await hass.async_block_till_done()

        entry = hass.config_entries.async_get_entry(entry.entry_id)
        assert entry is not None
        assert entry.state is ConfigEntryState.LOADED
        subentry = next(
            item
            for item in entry.subentries.values()
            if item.subentry_type == "conversation"
        )
        agent = conversation.async_get_agent(hass, entry.entry_id)
        assert agent is not None

        configured = agent_config.configured_function_tools_from_data(subentry.data)
        function_tool = next(
            tool for tool in configured if tool["spec"]["name"] == _TOOL_NAME
        )

        user = await hass.auth.async_create_user("Delayed Restart User")
        tool_input = llm.ToolInput(
            id="call-delayed-process-restart",
            tool_name=_TOOL_NAME,
            tool_args={"marker": _MARKER, "delay": {"seconds": 30}},
            external=True,
        )
        llm_context = SimpleNamespace(
            context=Context(user_id=user.id),
            device_id=_DEVICE_ID,
        )

        await agent._execute_function_tool(
            function_tool,
            tool_input,
            llm_context,
            [],
        )

        manager = hass.data[DOMAIN][delayed_tools.DATA_DELAYED_TOOL_MANAGER]
        assert len(manager._records) == 1
        record = next(iter(manager._records.values()))
        assert record.status == "pending"
        assert record.entry_id == entry.entry_id
        assert record.subentry_id == subentry.subentry_id
        assert record.tool_name == _TOOL_NAME
        assert record.user_id == user.id
        assert record.device_id == _DEVICE_ID
        assert record.arguments == {"marker": _MARKER, "delay": {"seconds": 30}}

        (config_dir / _METADATA_FILE).write_text(
            json.dumps(
                {
                    "entry_id": entry.entry_id,
                    "subentry_id": subentry.subentry_id,
                    "user_id": user.id,
                    "call_id": record.call_id,
                }
            ),
            encoding="utf-8",
        )
    finally:
        await hass.async_stop()


async def _register_execution_probe(hass: Any, config_dir: Path) -> None:
    """Register a local service whose calls survive as a parent-readable marker."""

    async def record(call: Any) -> None:
        payload = {
            "marker": call.data.get("marker"),
            "user_id": call.context.user_id,
        }
        path = config_dir / _EXECUTIONS_FILE
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    hass.services.async_register(_SERVICE_DOMAIN, _SERVICE_NAME, record)


async def _recover_and_execute(config_dir: Path) -> None:
    """Second process: recover the now-due pending call and execute it once."""
    from homeassistant import bootstrap, runner
    from homeassistant.config_entries import ConfigEntryState

    sys.path.insert(0, str(config_dir))
    metadata = json.loads((config_dir / _METADATA_FILE).read_text(encoding="utf-8"))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=True)
    )
    assert hass is not None
    await _register_execution_probe(hass, config_dir)
    await hass.async_start()

    try:
        delayed_tools = importlib.import_module(
            f"custom_components.{DOMAIN}.delayed_tools"
        )
        _assert_source_component(config_dir)
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.entry_id == metadata["entry_id"]
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        manager = hass.data[DOMAIN][delayed_tools.DATA_DELAYED_TOOL_MANAGER]
        for _ in range(100):
            await hass.async_block_till_done()
            if not manager._records and (config_dir / _EXECUTIONS_FILE).exists():
                break
            await asyncio.sleep(0.05)

        assert manager._records == {}
        executions = _read_executions(config_dir)
        assert executions == [
            {"marker": _MARKER, "user_id": metadata["user_id"]}
        ]
    finally:
        await hass.async_stop()


async def _child_main() -> None:
    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    phase = os.environ[_CHILD_PHASE]
    if phase == "schedule":
        await _create_entry_and_schedule(config_dir)
    elif phase == "execute":
        await _recover_and_execute(config_dir)
    else:
        raise AssertionError(f"Unknown delayed-tool process phase: {phase}")


def _run_child(config_dir: Path, phase: str) -> subprocess.CompletedProcess[str]:
    """Run one completely independent Home Assistant Python process."""
    return run_python_child(
        __file__,
        cwd=config_dir,
        extra_env={
            _CHILD_PHASE: phase,
            _CONFIG_DIR_ENV: str(config_dir),
        },
        timeout=30,
    )


def _storage_path(config_dir: Path) -> Path:
    return config_dir / ".storage" / _STORE_KEY


def _read_store(config_dir: Path) -> dict[str, Any]:
    return json.loads(_storage_path(config_dir).read_text(encoding="utf-8"))


def _write_store(config_dir: Path, payload: dict[str, Any]) -> None:
    _storage_path(config_dir).write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8"
    )


def _read_executions(config_dir: Path) -> list[dict[str, Any]]:
    path = config_dir / _EXECUTIONS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _assert_child_ok(result: subprocess.CompletedProcess[str], phase: str) -> None:
    assert result.returncode == 0, (
        f"delayed-tool HA process phase {phase!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


def test_delayed_tool_survives_process_restart_and_never_replays_execution_boundary(
    tmp_path: Path,
) -> None:
    """A pending delayed call survives a real restart and executes exactly once."""
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    _stage_component(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n  name: Delayed Tool Process Restart\n",
        encoding="utf-8",
    )

    scheduled = _run_child(config_dir, "schedule")
    _assert_child_ok(scheduled, "schedule")

    persisted = _read_store(config_dir)
    calls = persisted["data"]["calls"]
    assert len(calls) == 1
    original = calls[0]
    assert original["status"] == "pending"
    assert original["tool_name"] == _TOOL_NAME
    assert original["device_id"] == _DEVICE_ID

    calls[0]["due_at"] = _PAST_DUE
    _write_store(config_dir, persisted)

    executed = _run_child(config_dir, "execute")
    _assert_child_ok(executed, "execute")
    assert len(_read_executions(config_dir)) == 1
    after_execution = _read_store(config_dir)
    assert after_execution["data"]["calls"] == []



if __name__ == "__main__" and os.environ.get(_CHILD_PHASE):
    asyncio.run(_child_main())
