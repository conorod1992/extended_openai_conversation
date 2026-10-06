"""Process-level acceptance for Home Assistant version upgrades with fixed EOAI code."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import yaml

DOMAIN = "extended_openai_conversation_responses"
_CHILD_PHASE_ENV = "HA_VERSION_UPGRADE_CHILD_PHASE"
_CONFIG_DIR_ENV = "HA_VERSION_UPGRADE_CONFIG_DIR"
_EXPECTED_HA_ENV = "HA_VERSION_UPGRADE_EXPECTED_HA"
_RUNTIME_MATRIX_ENV = "HA_VERSION_UPGRADE_RUNTIMES"
_EVIDENCE_FILE_ENV = "HA_VERSION_UPGRADE_EVIDENCE_FILE"
_STATE_FILE = "upgrade-acceptance-state.json"

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse the established release-upgrade assertions without making this lane depend on
# a released EOAI payload. Setting the helper child marker prevents pytest-only imports
# when this file is executed inside the isolated historical HA virtualenv.
os.environ.setdefault("UPGRADE_ACCEPTANCE_CHILD_PHASE", "ha-version-upgrade-helper")
from tests_real_ha import test_release_upgrade_acceptance as upgrade_helpers  # noqa: E402

if not os.environ.get(_CHILD_PHASE_ENV):
    import pytest

    pytestmark = pytest.mark.skipif(
        not os.environ.get(_RUNTIME_MATRIX_ENV),
        reason="requires HA_VERSION_UPGRADE_RUNTIMES",
    )


def _runtime_matrix() -> list[dict[str, str]]:
    raw = os.environ.get(_RUNTIME_MATRIX_ENV, "")
    data = json.loads(raw)
    assert isinstance(data, list) and len(data) >= 2
    runtimes: list[dict[str, str]] = []
    for item in data:
        assert isinstance(item, dict)
        python = item.get("python")
        expected = item.get("version")
        label = item.get("label")
        assert isinstance(python, str) and python
        assert isinstance(expected, str) and expected
        assert isinstance(label, str) and label
        runtimes.append({"python": python, "version": expected, "label": label})
    assert len({item["version"] for item in runtimes}) == len(runtimes)
    return runtimes


def _conversation_subentry(entry: Any) -> Any:
    return next(
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    )


def _delayed_tool_config() -> dict[str, Any]:
    return {
        "spec": {
            "name": "ha_upgrade_delayed_marker",
            "description": "Persist a harmless delayed marker across HA upgrades.",
            "parameters": {
                "type": "object",
                "properties": {
                    "marker": {"type": "string"},
                    "delay": {
                        "type": "object",
                        "properties": {"hours": {"type": "number", "minimum": 0}},
                        "required": ["hours"],
                        "additionalProperties": False,
                    },
                },
                "required": ["marker", "delay"],
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "template",
            "value_template": "{{ marker }}",
        },
        "enabled": True,
    }


def _read_tools(raw: Any) -> list[dict[str, Any]]:
    parsed = yaml.safe_load(raw) if isinstance(raw, str) else raw
    return [dict(item) for item in (parsed or []) if isinstance(item, dict)]


async def _append_archive_history(hass: Any, entry_id: str, owner_id: str) -> dict[str, Any]:
    from homeassistant.components import conversation
    from homeassistant.core import Context

    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None
    assert agent._archive is not None

    async def model(log: Any, **kwargs: Any) -> None:
        del kwargs
        log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=agent.entity_id,
                content="HA upgrade archive response",
            )
        )

    original = agent._async_handle_chat_log
    agent._async_handle_chat_log = model
    conversation_id = None
    try:
        for index in range(2):
            result = await conversation.async_converse(
                hass=hass,
                text=f"HA upgrade archive turn {index}",
                conversation_id=conversation_id,
                context=Context(user_id=owner_id),
                language="en",
                agent_id=entry_id,
            )
            assert result.response.error_code is None
            conversation_id = result.conversation_id
            assert conversation_id
    finally:
        agent._async_handle_chat_log = original

    sessions = await agent._archive.async_list_sessions(f"user:{owner_id}")
    assert len(sessions["sessions"]) == 1
    session = sessions["sessions"][0]
    assert session["turn_count"] == 2
    detail = await agent._archive.async_get(f"user:{owner_id}", session["session_id"])
    assert [turn["user_text"] for turn in detail["turns"]] == [
        "HA upgrade archive turn 0",
        "HA upgrade archive turn 1",
    ]
    return {
        "session_id": session["session_id"],
        "conversation_id": conversation_id,
        "turn_count": 2,
    }


async def _schedule_durable_call(hass: Any, entry_id: str, owner_id: str) -> dict[str, Any]:
    from homeassistant.components import conversation
    from homeassistant.core import Context
    from homeassistant.helpers import llm
    from custom_components.extended_openai_conversation_responses import delayed_tools

    agent = conversation.async_get_agent(hass, entry_id)
    assert agent is not None
    tools = upgrade_helpers._tool_names
    del tools  # Document that the helper module owns the canonical tool parsing contract.

    configured = _read_tools(agent.subentry.data.get("function_tools"))
    function_tool = next(
        item
        for item in configured
        if item.get("spec", {}).get("name") == "ha_upgrade_delayed_marker"
    )
    manager = hass.data[DOMAIN][delayed_tools.DATA_DELAYED_TOOL_MANAGER]
    record = await manager.async_schedule(
        agent,
        "ha_upgrade_delayed_marker",
        {"marker": "PERSISTED_HA_UPGRADE_DELAY", "delay": {"hours": 24}},
        SimpleNamespace(
            context=Context(user_id=owner_id),
            device_id="ha-version-upgrade-device",
        ),
        function_tool=function_tool,
    )
    assert record.status == "pending"
    return {
        "call_id": record.call_id,
        "tool_name": record.tool_name,
        "due_at": record.due_at,
        "created_at": record.created_at,
    }


async def _seed_augmented_state(hass: Any, config_dir: Path) -> None:
    """Create representative candidate-owned state in the oldest supported HA."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.components import conversation
    from custom_components.extended_openai_conversation_responses import agent_config, const

    await upgrade_helpers._released_phase(hass, config_dir)
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))

    entry = hass.config_entries.async_get_entry(state["entry_id"])
    assert entry is not None and entry.state is ConfigEntryState.LOADED
    subentry = _conversation_subentry(entry)

    data = dict(subentry.data)
    tools = _read_tools(data.get(const.CONF_FUNCTION_TOOLS))
    if not any(
        item.get("spec", {}).get("name") == "ha_upgrade_delayed_marker"
        for item in tools
    ):
        tools.append(_delayed_tool_config())

    updates = {
        const.CONF_FUNCTION_TOOLS: yaml.safe_dump(
            tools, sort_keys=False, allow_unicode=True
        ),
        const.CONF_FUNCTION_GROUPS: [
            {
                "id": "ha_upgrade_tools",
                "name": "HA Upgrade Tools",
                "description": "Tools retained while Home Assistant changes version.",
                "loading_mode": "always",
                "functions": ["upgrade_marker", "ha_upgrade_delayed_marker"],
                "enabled": True,
            }
        ],
        const.CONF_ARCHIVE_ENABLED: True,
        const.CONF_ARCHIVE_RETENTION_DAYS: 30,
    }
    merged = agent_config.merge_agent_config(data, updates)
    hass.config_entries.async_update_subentry(entry, subentry, data=merged)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    archive = await _append_archive_history(hass, entry.entry_id, state["owner_id"])
    delayed = await _schedule_durable_call(
        hass, entry.entry_id, state["owner_id"]
    )

    state.update(
        {
            "archive": archive,
            "delayed": delayed,
            "function_group": "ha_upgrade_tools",
            "ha_upgrade_versions_seen": [version("homeassistant")],
            "ha_upgrade_migration_complete": False,
        }
    )
    (config_dir / _STATE_FILE).write_text(
        json.dumps(state, sort_keys=True), encoding="utf-8"
    )


async def _assert_augmented_state(hass: Any, config_dir: Path) -> None:
    from homeassistant.components import conversation
    from homeassistant.config_entries import ConfigEntryState
    from custom_components.extended_openai_conversation_responses import const, delayed_tools

    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    entry = hass.config_entries.async_get_entry(state["entry_id"])
    assert entry is not None
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id == state["entry_id"]

    subentry = _conversation_subentry(entry)
    assert subentry.subentry_id == state["subentry_id"]
    assert subentry.data[const.CONF_ARCHIVE_ENABLED] is True
    assert subentry.data[const.CONF_ARCHIVE_RETENTION_DAYS] == 30

    groups = subentry.data[const.CONF_FUNCTION_GROUPS]
    group = next(item for item in groups if item["id"] == state["function_group"])
    assert group["loading_mode"] == "always"
    assert set(group["functions"]) == {
        "upgrade_marker",
        "ha_upgrade_delayed_marker",
    }

    agent = conversation.async_get_agent(hass, entry.entry_id)
    assert agent is not None
    sessions = await agent._archive.async_list_sessions(f"user:{state['owner_id']}")
    session = next(
        item
        for item in sessions["sessions"]
        if item["session_id"] == state["archive"]["session_id"]
    )
    assert session["turn_count"] == state["archive"]["turn_count"]
    detail = await agent._archive.async_get(
        f"user:{state['owner_id']}", state["archive"]["session_id"]
    )
    assert [turn["user_text"] for turn in detail["turns"]] == [
        "HA upgrade archive turn 0",
        "HA upgrade archive turn 1",
    ]

    manager = hass.data[DOMAIN][delayed_tools.DATA_DELAYED_TOOL_MANAGER]
    record = manager._records.get(state["delayed"]["call_id"])
    assert record is not None
    assert record.status == "pending"
    assert record.entry_id == state["entry_id"]
    assert record.subentry_id == state["subentry_id"]
    assert record.tool_name == "ha_upgrade_delayed_marker"
    assert record.arguments["marker"] == "PERSISTED_HA_UPGRADE_DELAY"


async def _transition_phase(hass: Any, config_dir: Path) -> None:
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    if not state.get("ha_upgrade_migration_complete"):
        await upgrade_helpers._candidate_migration_phase(hass, config_dir)
        state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
        state["ha_upgrade_migration_complete"] = True
        (config_dir / _STATE_FILE).write_text(
            json.dumps(state, sort_keys=True), encoding="utf-8"
        )
    else:
        await upgrade_helpers._candidate_restart_phase(hass, config_dir)

    await _assert_augmented_state(hass, config_dir)
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    state.setdefault("ha_upgrade_versions_seen", []).append(version("homeassistant"))
    (config_dir / _STATE_FILE).write_text(
        json.dumps(state, sort_keys=True), encoding="utf-8"
    )


async def _restart_phase(hass: Any, config_dir: Path) -> None:
    await upgrade_helpers._candidate_restart_phase(hass, config_dir)
    await _assert_augmented_state(hass, config_dir)


async def _child_main() -> None:
    from homeassistant import bootstrap, runner

    config_dir = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    sys.path.insert(0, str(config_dir))

    expected = os.environ[_EXPECTED_HA_ENV]
    actual = version("homeassistant")
    assert actual == expected, f"expected Home Assistant {expected}, got {actual}"

    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
    )
    assert hass is not None
    await hass.async_start()
    try:
        module = __import__(f"custom_components.{DOMAIN}", fromlist=["__file__"])
        module_path = Path(module.__file__).resolve()
        component_root = (config_dir / "custom_components" / DOMAIN).resolve()
        assert component_root in module_path.parents

        phase = os.environ[_CHILD_PHASE_ENV]
        if phase == "seed":
            await _seed_augmented_state(hass, config_dir)
        elif phase == "transition":
            await _transition_phase(hass, config_dir)
        elif phase == "restart":
            await _restart_phase(hass, config_dir)
        else:
            raise AssertionError(f"unknown HA version-upgrade phase: {phase}")
        await hass.async_block_till_done()
    finally:
        await hass.async_stop()


def _run_child(
    config_dir: Path,
    runtime: dict[str, str],
    phase: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env[_CHILD_PHASE_ENV] = phase
    env[_CONFIG_DIR_ENV] = str(config_dir)
    env[_EXPECTED_HA_ENV] = runtime["version"]
    env["UPGRADE_ACCEPTANCE_CHILD_PHASE"] = "ha-version-upgrade-helper"
    existing = env.get("PYTHONPATH")
    roots = [str(config_dir), str(REPO_ROOT)]
    if existing:
        roots.extend(existing.split(os.pathsep))
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(roots))
    return subprocess.run(
        [runtime["python"], str(Path(__file__).resolve())],
        cwd=config_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=240,
        check=False,
    )


def _assert_child_ok(
    result: subprocess.CompletedProcess[str],
    runtime: dict[str, str],
    phase: str,
) -> None:
    assert result.returncode == 0, (
        f"HA {runtime['label']} ({runtime['version']}) phase {phase!r} failed\n"
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )


def _write_evidence(
    *,
    runtimes: list[dict[str, str]],
    config_dir: Path,
    candidate_digest: str,
) -> None:
    target = os.environ.get(_EVIDENCE_FILE_ENV)
    if not target:
        return
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    payload = {
        "candidate_sha": os.environ.get("GITHUB_SHA"),
        "candidate_manifest_version": json.loads(
            (
                REPO_ROOT
                / "custom_components"
                / DOMAIN
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )["version"],
        "candidate_component_digest": candidate_digest,
        "runtimes": [
            {"label": item["label"], "homeassistant": item["version"]}
            for item in runtimes
        ],
        "entry_id": state["entry_id"],
        "subentry_id": state["subentry_id"],
        "archive_session_id": state["archive"]["session_id"],
        "delayed_call_id": state["delayed"]["call_id"],
        "versions_seen": state["ha_upgrade_versions_seen"],
    }
    Path(target).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_candidate_survives_home_assistant_version_upgrades(
    socket_enabled: Any,
    tmp_path: Path,
    unused_tcp_port: int,
) -> None:
    """Carry one populated candidate installation across real HA runtime upgrades."""
    del socket_enabled
    runtimes = _runtime_matrix()
    source = REPO_ROOT / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-version-upgrade-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(source, destination)
    (config_dir / "configuration.yaml").write_text(
        "homeassistant:\n"
        "  name: HA Version Upgrade Acceptance\n"
        "recorder:\n"
        "http:\n"
        "  server_host: 127.0.0.1\n"
        f"  server_port: {unused_tcp_port}\n",
        encoding="utf-8",
    )

    candidate_digest = upgrade_helpers._component_digest(source)
    assert candidate_digest == upgrade_helpers._component_digest(destination)

    seeded = _run_child(config_dir, runtimes[0], "seed")
    _assert_child_ok(seeded, runtimes[0], "seed")
    assert (config_dir / ".storage" / "core.config_entries").exists()
    assert (config_dir / _STATE_FILE).exists()

    for runtime in runtimes[1:]:
        transitioned = _run_child(config_dir, runtime, "transition")
        _assert_child_ok(transitioned, runtime, "transition")

        restarted = _run_child(config_dir, runtime, "restart")
        _assert_child_ok(restarted, runtime, "restart")

    # The integration payload is never replaced during this test. Only HA itself changes.
    assert candidate_digest == upgrade_helpers._component_digest(destination)
    state = json.loads((config_dir / _STATE_FILE).read_text(encoding="utf-8"))
    assert state["ha_upgrade_versions_seen"][0] == runtimes[0]["version"]
    assert [item["version"] for item in runtimes[1:]] == state[
        "ha_upgrade_versions_seen"
    ][1:]
    _write_evidence(
        runtimes=runtimes,
        config_dir=config_dir,
        candidate_digest=candidate_digest,
    )


if __name__ == "__main__" and os.environ.get(_CHILD_PHASE_ENV):
    asyncio.run(_child_main())
